import json
import unittest

from core.data.live_mcp_adapter import DaisoMcpAdapter, LiveDataError
from core.data.mcp_client import McpClient, McpRequestError


class FakeResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, payload):
        self.text = "event: message\ndata: " + json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.headers = {}

    def post(self, url, json, timeout):
        self.calls.append(json)
        return FakeResponse(self.payloads.pop(0))


class LiveAdapterTests(unittest.TestCase):
    def test_sse_response_and_structured_content(self):
        session = FakeSession([{"jsonrpc": "2.0", "id": "1", "result": {
            "structuredContent": {"products": [{"name": "신라면"}]}}}])
        result = McpClient(session=session, min_interval=0).call("x", {})
        self.assertEqual(result["products"][0]["name"], "신라면")

    def test_cu_code_joins_price_and_store_stock(self):
        data = {"jsonrpc": "2.0", "id": "1", "result": {"structuredContent": {
            "inventory": {"available": True, "items": [
                {"itemCode": "8801", "itemName": "농심)신라면", "price": 1000}]},
            "nearbyStores": {"available": True, "stockItemCode": "8801", "stores": [
                {"storeCode": "C01", "storeName": "CU 중앙점", "address": "서울 관악구",
                 "latitude": 37.45, "longitude": 126.95, "distanceM": 240, "stock": 4}]}}}}
        session = FakeSession([data])
        adapter = DaisoMcpAdapter("CU", mcp_client=McpClient(session=session, min_interval=0))
        rows = adapter.search("신라면", 37.45, 126.95)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].price, 1000)
        self.assertEqual(rows[0].stock, 4)
        self.assertEqual(rows[0].product_code, "8801")
        self.assertEqual(rows[0].barcode, "")
        self.assertEqual(session.calls[0]["params"]["name"], "cu_check_inventory")

    def test_cu_refuses_mismatched_stock_code(self):
        payload = {"inventory": {"available": True, "items": [{"itemCode": "A", "itemName": "콜라", "price": 1000}]},
                   "nearbyStores": {"available": True, "stockItemCode": "B", "stores": []}}
        with self.assertRaises(LiveDataError):
            DaisoMcpAdapter._parse_cu(payload, "콜라", "now")

    def test_stock_parser_distinguishes_unknown_from_zero(self):
        self.assertEqual(DaisoMcpAdapter._parse_stock(0, {}), (0, True))
        self.assertEqual(DaisoMcpAdapter._parse_stock("unknown", {}), (0, False))
        self.assertEqual(DaisoMcpAdapter._parse_stock("품절", {}), (0, True))

    def test_gs25_current_inventory_fields_are_read(self):
        payload = {"data": {"inventory": {"stores": [{
            "storeCode": "G01", "storeName": "GS25 중앙점", "address": "서울 중구",
            "realStockQuantity": 5, "searchItemSellPrice": 2500,
        }]}}}
        adapter = DaisoMcpAdapter("GS25")
        rows = adapter._extract_inventory_records(payload, "콜라", "now",
                                                  {"itemName": "콜라", "itemCode": "8801234567890"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stock, 5)
        self.assertEqual(rows[0].price, 2500)
        self.assertEqual(rows[0].product_code, "8801234567890")

    def test_cu_searches_each_matching_product_without_mixing_stock(self):
        class FakeMcp:
            def call(self, _tool, arguments):
                first = {"itemCode": "A", "itemName": "초코바 아몬드", "price": 1000}
                second = {"itemCode": "B", "itemName": "초코바 땅콩", "price": 1200}
                offset = arguments.get("offset", 0)
                items = [first, second] if arguments["size"] == 20 else [second]
                selected = first if offset == 0 else second
                return {"inventory": {"available": True, "totalCount": 2, "items": items},
                        "nearbyStores": {"available": True, "stockItemCode": selected["itemCode"],
                                         "stores": [{"storeCode": "S1", "storeName": "CU 한곳",
                                                     "stock": 3 if offset == 0 else 7}]}}

        rows = DaisoMcpAdapter("CU", mcp_client=FakeMcp()).search("초코바", 37.5, 127.0)
        self.assertEqual({row.product_code: (row.price, row.stock) for row in rows},
                         {"A": (1000, 3), "B": (1200, 7)})

    def test_gs25_searches_all_literal_matches_by_product_code(self):
        adapter = DaisoMcpAdapter("GS25")

        def fake_rest(path, params):
            if path.endswith("/products"):
                return {"data": {"products": [
                    {"itemCode": "A", "itemName": "초코바 아몬드"},
                    {"itemCode": "B", "itemName": "초코바 땅콩"},
                    {"itemCode": "C", "itemName": "초코 과자"},
                ]}}
            return {"data": {"inventory": {"stores": [{"storeCode": "G1", "storeName": "GS 한곳",
                                                      "realStockQuantity": 2 if params["itemCode"] == "A" else 5}]}}}

        adapter._rest = fake_rest
        rows = adapter.search("초코바", 37.5, 127.0)
        self.assertEqual({row.product_code: row.stock for row in rows}, {"A": 2, "B": 5})

    def test_emart_stops_when_inventory_ignores_requested_stores(self):
        class FakeMcp:
            def __init__(self):
                self.inventory_calls = 0

            def call(self, tool, arguments):
                if tool == "emart24_search_products":
                    return {"products": [
                        {"pluCd": "A", "goodsName": "초코바 아몬드", "viewPrice": 1000},
                        {"pluCd": "B", "goodsName": "초코바 땅콩", "viewPrice": 1200},
                    ], "totalCount": 2}
                if tool == "emart24_find_nearby_stores":
                    return {"stores": [{"bizNo": "LOCAL", "storeName": "이마트24 관악점",
                                        "address": "서울특별시 관악구", "latitude": 37.45,
                                        "longitude": 126.95}]}
                self.inventory_calls += 1
                self.assert_requested_store(arguments)
                return {"inventory": {"available": True, "stores": [
                    {"bizNo": "OTHER", "bizQty": 5}]}}

            @staticmethod
            def assert_requested_store(arguments):
                if arguments["bizNoArr"] != "LOCAL":
                    raise AssertionError(arguments)

        mcp = FakeMcp()
        adapter = DaisoMcpAdapter("이마트24", mcp_client=mcp,
                                  emart_region=("서울특별시", "관악구"))
        with self.assertRaisesRegex(LiveDataError, "요청한 지역 점포"):
            adapter.search("초코바", 37.45, 126.95)
        self.assertEqual(mcp.inventory_calls, 1)

    def test_upstream_mojibake_repair(self):
        self.assertEqual(McpClient._repair_text("ì ë¼ë©´"), "신라면")


if __name__ == "__main__":
    unittest.main()
