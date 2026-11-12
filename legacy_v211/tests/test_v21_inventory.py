from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import threading
import time
import unittest

import requests

from core.cart_models import CatalogLink
from core.data.data_manager import DataManager
from core.data.live_mcp_adapter import DaisoMcpAdapter, LiveDataError
from core.data.mcp_client import McpClient, McpRequestError
from core.models import StoreProduct


def observation(code="P", stock=2, store="S"):
    return StoreProduct("CU", "확인점", "서울 주소", "선택 상품 250ml", product_code=code,
                        store_code=store, stock=stock, price=1200, data_source="LIVE",
                        stock_updated_at="2020-01-01T00:00:00+09:00",
                        updated_at="2020-01-01T00:00:00+09:00")


class EmptyPromotions:
    def search(self, *_args, **_kwargs):
        return []


class Adapter:
    request_count = 1
    lookup_failures = ()
    catalog_incomplete = False

    def __init__(self, brand="CU", rows=None):
        self.brand = brand
        self.rows = [observation()] if rows is None else rows

    def search(self, *_args):
        return self.rows

    def check_selected(self, *_args):
        return self.rows


class V21InventoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.manager = self.new_manager("one")
        self.link = CatalogLink("item", "CU", "", "P", "선택 상품 250ml")

    def new_manager(self, name):
        return DataManager(Path(self.folder.name)/"demo.csv", "LIVE",
                           cache_path=Path(self.folder.name)/(name+".json"), promotion_manager=EmptyPromotions())

    def test_primary_success_zero_and_empty_do_not_call_secondary(self):
        for rows in ([observation()], [observation(stock=0)], []):
            manager = self.new_manager(str(len(rows))+str(bool(rows and rows[0].stock)))
            with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Adapter(rows=rows)), \
                 patch("core.data.data_manager.CompositeInventoryAdapter") as secondary:
                found, statuses = manager.search_selected(self.link, 37, 127)
            self.assertEqual(found, rows)
            self.assertEqual(statuses[0].source, "LIVE")
            self.assertFalse(statuses[0].fallback_used)
            secondary.assert_not_called()
            if rows and not rows[0].stock:
                self.assertTrue(found[0].stock_known)

    def test_error_uses_secondary_and_records_only_sanitized_metrics(self):
        logs = []
        self.manager.log = logs.append
        with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=LiveDataError("url?token=secret")), \
             patch("core.data.data_manager.CompositeInventoryAdapter", return_value=Adapter()) as secondary:
            found, statuses = self.manager.search_selected(self.link, 37, 127)
        self.assertEqual(len(found), 1)
        self.assertEqual(statuses[0].source, "LIVE")
        self.assertTrue(statuses[0].fallback_used)
        self.assertEqual(len(self.manager.metrics), 2)
        self.assertEqual([m["success"] for m in self.manager.metrics], [False, True])
        self.assertNotIn("secret", "".join(logs))
        secondary.assert_called_once()

    def test_explicit_gap_fallback_merges_without_duplicate_product_store(self):
        primary = Adapter(rows=[observation()])
        primary.catalog_incomplete = True
        newer = replace(observation(stock=4), stock_updated_at="2021-01-01T00:00:00+09:00")
        secondary = Adapter(rows=[newer, observation(store="S2")])
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=primary), \
             patch("core.data.data_manager.CompositeInventoryAdapter", return_value=secondary):
            found, statuses = self.manager.search_selected(self.link, 37, 127)
        self.assertEqual(len(found), 2)
        self.assertEqual(next(row.stock for row in found if row.store_code == "S"), 4)
        self.assertTrue(statuses[0].fallback_used)

    def test_identical_concurrent_requests_share_inflight_and_return_copies(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class Slow(Adapter):
            def check_selected(_self, *args):
                calls.append(args)
                entered.set()
                release.wait(2)
                return [observation()]
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Slow()), \
             patch("core.data.data_manager.CompositeInventoryAdapter") as secondary:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(self.manager.search_selected, self.link, 37, 127) for _ in range(6)]
                self.assertTrue(entered.wait(1))
                release.set()
                results = [future.result(2) for future in futures]
        self.assertEqual(len(calls), 1)
        results[0][0][0].stock = 999
        self.assertEqual(results[1][0][0].stock, 2)
        secondary.assert_not_called()

    def test_different_codes_locations_and_sessions_never_share(self):
        calls = []
        class Echo(Adapter):
            def check_selected(_self, link, lat, lng):
                calls.append((link.product_code, lat, lng))
                return [replace(observation(link.product_code), address=str(lat))]
        cases = [(self.manager, self.link, 37.000001),
                 (self.manager, replace(self.link, product_code="Q"), 37.000001),
                 (self.manager, self.link, 37.000002),
                 (self.new_manager("two"), self.link, 37.000001)]
        with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=Echo):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda case: case[0].search_selected(case[1], case[2], 127), cases))
        self.assertEqual(len(calls), 4)
        self.assertEqual(results[1][0][0].product_code, "Q")
        self.assertEqual(results[2][0][0].address, "37.000002")

    def test_failure_is_shared_with_cooldown_but_not_other_condition(self):
        class Fails(Adapter):
            calls = 0
            def check_selected(_self, *args):
                Fails.calls += 1
                raise McpRequestError("장애", {"error": {"code": "TEMP"}, "token": "secret", "diagnostics": {"status": 502}})
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Fails()), \
             patch("core.data.data_manager.CompositeInventoryAdapter", return_value=Fails()):
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda _: self.manager.search_selected(self.link, 37, 127), range(5)))
            self.manager.search_selected(self.link, 37, 127, force_refresh=True)
            self.assertEqual(Fails.calls, 2)
            self.manager.search_selected(replace(self.link, product_code="Q"), 37, 127)
        self.assertEqual(Fails.calls, 4)
        self.assertTrue(all(result[1][0].source == "ERROR" and not result[0] for result in results))
        self.assertEqual(results[0][1][0].error_details["paths"][0]["details"]["diagnostics"]["status"], 502)
        self.assertNotIn("token", results[0][1][0].error_details["paths"][0]["details"])

    def test_cache_expiry_force_and_source_clock_are_distinct(self):
        adapter = Adapter()
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=adapter) as remote:
            with patch("core.data.data_manager.time.time", return_value=1000):
                first, _ = self.manager.search_selected(self.link, 37, 127)
            with patch("core.data.data_manager.time.time", return_value=1030):
                cached, statuses = self.manager.search_selected(self.link, 37, 127)
            self.assertEqual(remote.call_count, 1)
            self.assertTrue(cached[0].app_cache_used)
            self.assertEqual(statuses[0].cache_age_seconds, 30)
            self.assertEqual(cached[0].stock_updated_at, first[0].stock_updated_at)
            self.assertEqual(cached[0].received_at, first[0].received_at)
            with patch("core.data.data_manager.time.time", return_value=1031):
                self.manager.search_selected(self.link, 37, 127, force_refresh=True)
            self.assertEqual(remote.call_count, 2)
            with patch("core.data.data_manager.time.time", return_value=1092):
                self.manager.search_selected(self.link, 37, 127)
            self.assertEqual(remote.call_count, 3)

    def test_no_source_clock_is_fabricated_from_receipt_or_http_header(self):
        payload = {"fetchedAt": "2026-10-31T10:00:00+09:00", "_sourceMeta": {
            "date": "Sat, 31 Oct 2026 01:00:00 GMT", "age": "60"}}
        self.assertEqual(DaisoMcpAdapter._source_timestamp(payload, "fallback"), "")
        row = replace(observation(), stock_updated_at="", updated_at="2026-10-31T10:00:00+09:00")
        self.manager._mark_track_observation([row], "주", completion_time=True)
        self.assertEqual(row.stock_updated_at, "")
        self.assertTrue(row.received_at)

    def test_catalog_search_never_fetches_other_candidate_inventory(self):
        class FakeMcp:
            calls = []
            def call(_self, tool, arguments):
                _self.calls.append((tool, arguments))
                return {"standard": {"products": [{"code": "8801234567890", "name": "음료 250ml",
                                                     "price": 1500, "raw": {"itemCode": "8801234567890"}}]}}
        mcp = FakeMcp()
        rows = DaisoMcpAdapter("GS25", mcp_client=mcp).search_products("음료", 37, 127)
        self.assertEqual([call[0] for call in mcp.calls], ["search_products"])
        self.assertEqual(mcp.calls[0][1]["service"], "gs25")
        self.assertEqual(rows[0].product_code, "8801234567890")
        self.assertEqual(rows[0].barcode, "")
        self.assertEqual(rows[0].price_data_status, "REFERENCE")
        self.assertFalse(rows[0].has_store_identity)

    def test_selected_gs25_passes_code_without_keyword_discovery(self):
        class FakeMcp:
            calls = []
            def call(_self, tool, arguments):
                _self.calls.append((tool, arguments))
                return {"itemCode": "P", "inventory": {"stores": [{"storeCode": "S", "storeName": "점포", "realStockQuantity": 0}]}}
        mcp = FakeMcp()
        rows = DaisoMcpAdapter("GS25", mcp_client=mcp).check_selected(replace(self.link, brand="GS25"), 37, 127)
        self.assertEqual(mcp.calls, [("check_inventory", {"service": "gs25", "latitude": 37, "longitude": 127, "storeLimit": 20, "itemCode": "P", "keyword": self.link.product_name})])
        self.assertTrue(rows[0].stock_known)
        self.assertEqual(rows[0].stock, 0)
        self.assertEqual(rows[0].source_updated_at, "")

    def test_product_and_store_caches_are_independent_of_inventory(self):
        calls = []
        class Catalog(Adapter):
            def search_products(_self, *args):
                calls.append("product")
                return [replace(observation(), store_name="", store_code="", address="")]
            def find_stores(_self, *args):
                calls.append("store")
                return [{"code": "S", "name": "점포"}]
            def check_selected(_self, *args):
                calls.append("stock")
                return [observation()]
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Catalog()):
            for moment in [1000, 1061]:
                with patch("core.data.data_manager.time.time", return_value=moment):
                    self.manager.search_products("음료", 37, 127, ["CU"])
                    self.manager.find_stores("CU", 37, 127)
                    self.manager.search_selected(self.link, 37, 127)
        self.assertEqual(calls.count("product"), 1)
        self.assertEqual(calls.count("store"), 1)
        self.assertEqual(calls.count("stock"), 2)

    def test_network_retries_have_a_finite_two_attempt_bound(self):
        class Session:
            headers = {}
            count = 0
            def post(self, *args, **kwargs):
                self.count += 1
                raise requests.Timeout("timeout")
        session = Session()
        client = McpClient(session=session, timeout=.01, min_interval=0, retries=1)
        with patch("core.data.mcp_client.time.sleep"):
            with self.assertRaises(McpRequestError):
                client.call("check_inventory", {"service": "gs25", "itemCode": "P"})
        self.assertEqual(session.count, 2)
        self.assertEqual(client.request_count, 2)

    def test_equal_unknown_clocks_do_not_resolve_conflicting_stock(self):
        primary = Adapter(rows=[replace(observation(stock=2), stock_updated_at="")])
        primary.catalog_incomplete = True
        secondary = Adapter(rows=[replace(observation(stock=4), stock_updated_at="")])
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=primary), \
             patch("core.data.data_manager.CompositeInventoryAdapter", return_value=secondary):
            found, statuses = self.manager.search_selected(self.link, 37, 127)
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].stock_known)
        self.assertTrue(found[0].raw_payload["inventory_conflict"])
        self.assertEqual(statuses[0].source, "PARTIAL")

    def test_rate_limit_does_not_immediately_repeat_the_same_relay(self):
        class Limited(Adapter):
            def check_selected(_self, *_args):
                raise McpRequestError("제한", {"error": {"code": "RATE_LIMITED"}, "diagnostics": {"status": 429}})
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Limited()), \
             patch("core.data.data_manager.CompositeInventoryAdapter") as secondary:
            _, statuses = self.manager.search_selected(self.link, 37, 127)
        self.assertEqual(statuses[0].source, "ERROR")
        self.assertFalse(statuses[0].fallback_used)
        secondary.assert_not_called()

    def test_explicit_unavailable_empty_response_uses_recovery_and_keeps_reason(self):
        class UnavailableMcp:
            def call(_self, *_args, **_kwargs):
                return {"inventory": {"available": False, "unavailableReason": "원본 조회 일시 중단", "stores": []},
                        "diagnostics": {"status": 503}, "token": "must_remove"}
        primary = DaisoMcpAdapter("GS25", mcp_client=UnavailableMcp())
        secondary = Adapter(rows=[replace(observation(), brand="GS25")])
        with patch("core.data.data_manager.DaisoMcpAdapter", return_value=primary), \
             patch("core.data.data_manager.CompositeInventoryAdapter", return_value=secondary):
            rows, statuses = self.manager.search_selected(replace(self.link, brand="GS25"), 37, 127)
        self.assertEqual(len(rows), 1)
        self.assertTrue(statuses[0].fallback_used)
        details = statuses[0].error_details["paths"][0]["details"]
        self.assertEqual(details["error"]["code"], "INVENTORY_UNAVAILABLE")
        self.assertEqual(details["response"]["inventory"]["unavailableReason"], "원본 조회 일시 중단")
        self.assertEqual(details["response"]["diagnostics"]["status"], 503)
        self.assertNotIn("token", details["response"])


if __name__ == "__main__":
    unittest.main()
