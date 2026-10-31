import unittest
from unittest.mock import Mock, patch

from core.data.composite_inventory_adapter import CompositeInventoryAdapter
from core.data.live_mcp_adapter import DaisoMcpAdapter


class CompositeInventoryAdapterTests(unittest.TestCase):
    def test_emart_composite_response_keeps_store_and_zero_stock(self):
        adapter = DaisoMcpAdapter("이마트24")
        stores = {
            "E01": {"bizNo": "E01", "storeName": "이마트24 중앙점", "address": "서울 중구 주소",
                    "latitude": 37.5, "longitude": 127.0},
            "E02": {"bizNo": "E02", "storeName": "이마트24 시청점", "address": "서울 중구 다른 주소",
                    "latitude": 37.501, "longitude": 127.001},
        }
        rows = adapter._parse_emart_inventory({
            "pluCd": "8801234567890",
            "inventory": {"goodsInfo": {"viewPrice": 1500}, "stores": [{
                "bizNo": "E01", "bizQty": 0,
            }, {
                "bizNo": "E02", "bizQty": 4,
            }, {"bizNo": "E03", "bizQty": 9}]},
        }, {"pluCd": "8801234567890", "goodsName": "콜라", "viewPrice": 1500},
            stores, 37.5, 127.0, "2026-10-15T10:00:00+09:00")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].store_code, "E01")
        self.assertEqual(rows[0].stock, 0)
        self.assertTrue(rows[0].stock_known)
        self.assertEqual(rows[1].stock, 4)
        self.assertEqual(rows[1].price, 1500)

    def test_emart_direct_stock_uses_only_selected_region_stores(self):
        class FakeMcp:
            def __init__(self):
                self.calls = []

            def call(self, tool, arguments):
                self.calls.append((tool, arguments))
                if tool == "emart24_search_products":
                    return {"products": [{"pluCd": "8801234567890", "goodsName": "콜라 500ml",
                                          "viewPrice": 1500}], "totalCount": 1}
                if tool == "emart24_find_nearby_stores":
                    return {"stores": [
                        {"storeCode": "E01", "storeName": "관악점", "address": "서울특별시 관악구 주소",
                         "latitude": 37.46, "longitude": 126.95},
                        {"storeCode": "E99", "storeName": "다른지역점", "address": "서울특별시 서초구 주소",
                         "latitude": 37.50, "longitude": 127.00},
                    ]}
                raise AssertionError(tool)

        response = Mock()
        response.headers = {"Date": "Thu, 15 Oct 2026 00:00:00 GMT"}
        response.json.return_value = {
            "storeGoodsInfo": {"pluCd": "8801234567890", "viewPrice": 1500},
            "storeGoodsQty": [{"BIZNO": "E01", "BIZQTY": 3},
                              {"BIZNO": "E99", "BIZQTY": 8}],
        }
        mcp = FakeMcp()
        adapter = CompositeInventoryAdapter("이마트24", mcp_client=mcp,
                                            emart_region=("서울특별시", "관악구"))
        with patch("core.data.composite_inventory_adapter.requests.get", return_value=response) as request:
            rows = adapter.search("콜라", 37.46, 126.95)
        self.assertEqual([row.store_code for row in rows], ["E01"])
        self.assertEqual(rows[0].product_name, "콜라 500ml")
        self.assertEqual(rows[0].stock, 3)
        self.assertEqual(mcp.calls[1][1]["area2"], "관악구")
        self.assertEqual(request.call_args.kwargs["params"]["bizNoArr"], "E01")

    def test_seven_composite_response_maps_store_records(self):
        adapter = CompositeInventoryAdapter("세븐일레븐", geocoder=lambda *_: "서울 중구")
        rows = adapter._parse_seveneleven({
            "product": {"itemCode": "8801234567890", "itemName": "콜라", "salePrice": 1500},
            "inventory": {"stores": [{
                "storeCode": "S01", "storeName": "세븐일레븐 중앙점", "address": "서울 중구 주소",
                "stockQuantity": 3, "isSoldOut": False, "distanceM": 400,
            }]},
        }, "콜라", "2026-10-15T10:00:00+09:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].store_code, "S01")
        self.assertEqual(rows[0].stock, 3)
        self.assertEqual(rows[0].price, 1500)
        self.assertEqual(rows[0].product_code, "8801234567890")

    def test_gs25_composite_response_maps_stock_and_store(self):
        adapter = CompositeInventoryAdapter("GS25")
        rows = adapter._parse_gs25({
            "itemCode": "8801234567890",
            "product": {"name": "콜라", "sellPrice": 1500},
            "inventory": {"stores": [{
                "storeCode": "G01", "storeName": "GS25 중앙점", "address": "서울 중구 주소",
                "realStockQuantity": 3, "distanceM": 400,
            }]},
        }, "콜라", "2026-10-15T10:00:00+09:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].store_code, "G01")
        self.assertEqual(rows[0].stock, 3)
        self.assertEqual(rows[0].price, 1500)

    def test_gs25_track_searches_product_before_inventory_and_passes_item_code(self):
        class FakeMcp:
            def __init__(self):
                self.calls = []

            def call(self, tool, arguments):
                self.calls.append((tool, arguments))
                if tool == "gs25_search_products":
                    return {"products": [{"itemCode": "8801234567890", "itemName": "콜라 1.5L"}]}
                return {"itemCode": "8801234567890", "product": {"name": "콜라"},
                        "inventory": {"stores": [{"storeCode": "G01", "storeName": "GS25 중앙점",
                                                   "realStockQuantity": 2}]}}

        mcp = FakeMcp()
        rows = CompositeInventoryAdapter("GS25", mcp_client=mcp).search("콜라", 37.5, 127.0)
        self.assertEqual([call[0] for call in mcp.calls], ["gs25_search_products", "gs25_check_inventory"])
        self.assertEqual(mcp.calls[1][1]["itemCode"], "8801234567890")
        self.assertEqual(rows[0].stock, 2)
        self.assertEqual(rows[0].product_name, "콜라 1.5L")

    def test_http_cache_age_is_not_invented_as_source_observation_time(self):
        observed = CompositeInventoryAdapter._source_timestamp({
            "_sourceMeta": {"date": "Thu, 15 Oct 2026 00:00:00 GMT", "age": "60"},
        }, "fallback")
        self.assertEqual(observed, "")

    def test_cu_rest_payload_uses_same_product_store_join(self):
        from core.data.live_mcp_adapter import DaisoMcpAdapter

        rows = DaisoMcpAdapter._parse_cu({
            "inventory": {"available": True, "items": [{
                "itemCode": "8801234567890", "itemName": "콜라", "price": 1500,
            }]},
            "nearbyStores": {"available": True, "stockItemCode": "8801234567890", "stores": [{
                "storeCode": "C01", "storeName": "CU 중앙점", "address": "서울 중구 주소", "stock": 2,
            }]},
        }, "콜라", "2026-10-15T10:00:00+09:00")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].store_code, "C01")
        self.assertEqual(rows[0].stock, 2)
        self.assertEqual(rows[0].product_code, "8801234567890")


if __name__ == "__main__":
    unittest.main()
