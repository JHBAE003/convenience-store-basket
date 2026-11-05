"""Independent checks of stock semantics, metrics, and recovered diagnostics."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import time
import unittest

from core.cart_models import CatalogLink
from core.data.composite_inventory_adapter import CompositeInventoryAdapter
from core.data.data_manager import DataManager
from core.data.live_mcp_adapter import DaisoMcpAdapter, LiveDataError
from core.data.mcp_client import McpRequestError
from core.models import StoreProduct


class NoEvents:
    def search(self, *_args, **_kwargs):
        return []


def cu_payload(stock):
    return {"inventory": {"items": [{"itemCode": "P", "itemName": "콜라 500ml", "price": 1800}]},
            "nearbyStores": {"stockItemCode": "P", "stores": [
                {"storeCode": "S", "storeName": "CU 확인점", "address": "서울", "stock": stock}]}}


class IndependentInventoryReview(unittest.TestCase):
    def test_explicit_nested_stock_time_precedes_general_top_updated_time(self):
        result = DaisoMcpAdapter._source_timestamp({
            "updatedAt": "2026-10-31T12:00:00+09:00",
            "inventory": {"stockUpdatedAt": "2020-01-01T00:00:00+09:00"}})
        self.assertEqual(result, "2020-01-01T00:00:00+09:00")

    def test_cu_presence_does_not_invent_one_exact_unit(self):
        for stock in (True, "재고 있음"):
            with self.subTest(stock=stock):
                row = DaisoMcpAdapter._parse_cu(cu_payload(stock), "콜라", "2026-10-31T12:00:00+09:00")[0]
                self.assertTrue(row.stock_known)
                self.assertFalse(row.stock_quantity_exact)
                self.assertEqual(row.stock_display, "재고 있음")

    def test_cu_zero_remains_zero_and_catalog_price_remains_reference(self):
        row = DaisoMcpAdapter._parse_cu(cu_payload(0), "콜라", "2026-10-31T12:00:00+09:00")[0]
        self.assertTrue(row.stock_known)
        self.assertTrue(row.stock_quantity_exact)
        self.assertEqual(row.stock, 0)
        self.assertEqual(row.price_data_status, "REFERENCE")

    def test_secondary_failed_rest_requests_are_still_counted(self):
        class Response:
            status_code = 503

        class Session:
            headers = {}

            def get(self, *_args, **_kwargs):
                return Response()

        adapter = CompositeInventoryAdapter("GS25")
        link = CatalogLink("cola", "GS25", "", "P", "콜라 500ml")
        with patch("core.data.live_mcp_adapter.requests.Session", return_value=Session()), \
                patch("core.data.live_mcp_adapter.time.sleep"):
            with self.assertRaises(LiveDataError):
                adapter.check_selected(link, 37.5, 127)
        with TemporaryDirectory() as directory:
            manager = DataManager(Path(directory)/"demo.csv", "LIVE", cache_path=Path(directory)/"cache.json", promotion_manager=NoEvents())
            metric = manager._record_metric("GS25", "재고 보조", adapter, time.perf_counter(), False, True)
        self.assertEqual(metric["request_count"], 2)

    def test_primary_error_is_preserved_after_successful_recovery(self):
        class Primary:
            request_count = 1

            def __init__(self, brand):
                pass

            def search(self, *_args):
                raise McpRequestError("주 경로 실패", {"error": {"code": "E_PRIMARY"}, "api_key": "must_remove"})

        class Secondary:
            request_count = 1
            lookup_failures = []
            catalog_incomplete = False

            def __init__(self, brand):
                pass

            def search(self, *_args):
                return [StoreProduct("CU", "확인점", "서울", "콜라 500ml", product_code="P", stock=0)]

        with TemporaryDirectory() as directory:
            manager = DataManager(Path(directory)/"demo.csv", "LIVE", cache_path=Path(directory)/"cache.json", promotion_manager=NoEvents())
            with patch("core.data.data_manager.DaisoMcpAdapter", Primary), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", Secondary):
                _, statuses = manager.search_all("콜라", 37.5, 127, ["CU"], include_unpriced=True)
            self.assertTrue(statuses[0].fallback_used)
            details = json.dumps(statuses[0].error_details, ensure_ascii=False)
            self.assertIn("E_PRIMARY", details)
            self.assertNotIn("must_remove", details)

    def test_both_path_errors_are_preserved_without_credentials(self):
        class Failing:
            request_count = 1

            def __init__(self, brand, code):
                self.code = code

            def search(self, *_args):
                raise McpRequestError("실패", {"error": {"code": self.code}, "authorization": "must_remove"})

        with TemporaryDirectory() as directory:
            manager = DataManager(Path(directory)/"demo.csv", "LIVE", cache_path=Path(directory)/"cache.json", promotion_manager=NoEvents())
            with patch("core.data.data_manager.DaisoMcpAdapter", lambda brand: Failing(brand, "E_PRIMARY")), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", lambda brand: Failing(brand, "E_SECONDARY")):
                _, statuses = manager.search_all("콜라", 37.5, 127, ["CU"], include_unpriced=True)
            self.assertEqual(statuses[0].source, "ERROR")
            details = json.dumps(statuses[0].error_details, ensure_ascii=False)
            self.assertIn("E_PRIMARY", details)
            self.assertIn("E_SECONDARY", details)
            self.assertNotIn("must_remove", details)


if __name__ == "__main__":
    unittest.main()
