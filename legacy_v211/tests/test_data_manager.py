import tempfile
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import LIVE_CACHE_TTL_SECONDS
from core.data.data_manager import DataManager
from core.data.live_mcp_adapter import LiveDataError
from core.models import StoreProduct


class DataManagerTests(unittest.TestCase):
    def test_unpriced_store_is_hidden_from_live_and_cached_results(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache.json"
            manager = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            manager.promotion_manager.search = lambda *_args: []
            priced = StoreProduct("CU", "가격확인점", "주소", "콜라", price=1000, stock=2, store_code="P")
            unpriced = StoreProduct("CU", "가격미확인점", "주소", "콜라", price=0, stock=3, store_code="U")

            class Adapter:
                def search(self, *_args):
                    return [priced, unpriced]

            with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Adapter()), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", return_value=Adapter()):
                rows, statuses = manager.search_all("콜라", 37.0, 127.0, ["CU"])
            self.assertEqual([row.store_name for row in rows], ["가격확인점"])
            self.assertEqual(len(statuses[0].records), 1)
            self.assertIn("가격 미확인 상품 1건", statuses[0].message)
            self.assertEqual(len(json.loads(cache.read_text(encoding="utf-8"))[0]["records"]), 2)

            restored = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=AssertionError("원격 조회 불필요")), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=AssertionError("원격 조회 불필요")):
                cached_rows, cached_statuses = restored.search_all("콜라", 37.0, 127.0, ["CU"])
            self.assertEqual([row.store_name for row in cached_rows], ["가격확인점"])
            self.assertEqual(len(cached_statuses[0].records), 1)

    def test_live_failure_uses_persisted_cache_without_marking_live(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache.json"
            manager = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            manager.promotion_manager.search = lambda *_args: []
            row = StoreProduct("CU", "CU점", "주소", "콜라", price=1000, stock=2,
                               barcode="8801", store_code="C1", data_source="LIVE")
            class Adapter:
                def search(self, *args):
                    return [row]
            with patch("core.data.data_manager.DaisoMcpAdapter", return_value=Adapter()), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", return_value=Adapter()):
                records, status = manager.search_all("콜라", 37.0, 127.0, ["CU"])
            self.assertEqual(status[0].source, "LIVE")
            document = json.loads(cache.read_text(encoding="utf-8"))
            document[0]["saved_at"] -= LIVE_CACHE_TTL_SECONDS + 1
            cache.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            restored = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=LiveDataError("HTTP 503")), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=LiveDataError("HTTP 503")):
                cached, status = restored.search_all("콜라", 37.0, 127.0, ["CU"])
            self.assertEqual(status[0].source, "CACHE")
            self.assertEqual(cached[0].data_source, "CACHE")
            self.assertEqual(status[0].records[0].stock, 2)

    def test_recent_persisted_result_skips_only_that_brands_remote_lookup(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache.json"
            manager = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            manager.promotion_manager.search = lambda *_args: []

            class Adapter:
                def __init__(self, brand):
                    self.brand = brand

                def search(self, *_args):
                    return [StoreProduct(self.brand, self.brand + "점", "주소", "콜라",
                                         price=1000, stock=2, store_code=self.brand)]

            with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=Adapter), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=Adapter):
                manager.search_all("콜라", 37.0, 127.0, ["CU"])

            restored = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            restored.promotion_manager.search = lambda *_args: []
            requested = []

            class TrackingAdapter(Adapter):
                def search(self, *args):
                    requested.append(self.brand)
                    return super().search(*args)

            with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=TrackingAdapter), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=TrackingAdapter):
                rows, statuses = restored.search_all("콜라", 37.0, 127.0, ["CU", "GS25"])
            self.assertEqual(set(requested), {"GS25"})
            self.assertEqual([status.source for status in statuses], ["LIVE", "LIVE"])
            self.assertIsNotNone(statuses[0].cache_age_seconds)
            self.assertIsNone(statuses[1].cache_age_seconds)
            self.assertEqual([row.brand for row in rows], ["CU", "GS25"])
            self.assertEqual({entry["brand"] for entry in json.loads(cache.read_text(encoding="utf-8"))},
                             {"CU", "GS25"})

    def test_uncached_brands_are_queried_in_parallel_and_saved_together(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache.json"
            manager = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=cache)
            manager.promotion_manager.search = lambda *_args: []
            barrier = threading.Barrier(2, timeout=3)

            class Adapter:
                def __init__(self, brand):
                    self.brand = brand

                def search(self, *_args):
                    barrier.wait()
                    return [StoreProduct(self.brand, self.brand + "점", "주소", "콜라",
                                         price=1000, stock=2, store_code=self.brand)]

            with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=Adapter), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=Adapter):
                rows, statuses = manager.search_all("콜라", 37.0, 127.0, ["CU", "GS25"])
            self.assertEqual([status.brand for status in statuses], ["CU", "GS25"])
            self.assertEqual(len(rows), 2)
            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual({entry["brand"] for entry in saved}, {"CU", "GS25"})

    def test_explicit_primary_gap_uses_secondary_newer_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = DataManager(Path(folder) / "test.csv", "LIVE",
                                  cache_path=Path(folder) / "cache.json")
            manager.promotion_manager.search = lambda *_args: []
            older = StoreProduct("이마트24", "점포", "서울 주소", "콜라", product_code="8801", price=1000,
                                 store_code="E01", stock=1, stock_known=True,
                                 stock_updated_at="2026-10-15T09:00:00+09:00")
            newer = StoreProduct("이마트24", "점포", "서울 주소", "콜라", product_code="8801", price=1000,
                                 store_code="E01", stock=4, stock_known=True,
                                 stock_updated_at="2026-10-15T09:05:00+09:00",
                                 stock_source="Daiso MCP 통합조회 (Track 2)")

            class TrackAdapter:
                def __init__(self, rows):
                    self.rows = rows
                    self.catalog_incomplete = rows == [older]

                def search(self, *_args):
                    return self.rows

            with patch("core.services.geocoding.emart24_region_korean", return_value=("서울특별시", "중구")), \
                    patch("core.data.data_manager.DaisoMcpAdapter", return_value=TrackAdapter([older])), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", return_value=TrackAdapter([newer])):
                rows, status = manager.search_all("콜라", 37.0, 127.0, ["이마트24"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].stock, 4)
            self.assertIn("Track 2", rows[0].stock_source)
            self.assertTrue(status[0].fallback_used)

    def test_live_mode_failure_without_cache_is_error_not_test(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=Path(folder) / "cache.json")
            with patch("core.data.data_manager.DaisoMcpAdapter", side_effect=LiveDataError("HTTP 503")), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=LiveDataError("HTTP 503")):
                rows, status = manager.search_all("콜라", 37.0, 127.0, ["CU"])
            self.assertEqual(rows, [])
            self.assertEqual(status[0].source, "ERROR")

    def test_unverified_store_promotion_is_not_shown_when_inventory_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = DataManager(Path(folder) / "test.csv", "LIVE", cache_path=Path(folder) / "cache.json")
            price = StoreProduct("이마트24", "", "", "콜라", product_code="8801", price=1200,
                                 promotion="1+1", data_source="LIVE", price_data_status="LIVE")
            manager.promotion_manager.search = lambda *_args: [price]
            with patch("core.services.geocoding.emart24_region_korean", return_value=("서울특별시", "중구")), \
                    patch("core.data.data_manager.DaisoMcpAdapter", side_effect=LiveDataError("HTTP 502")), \
                    patch("core.data.data_manager.CompositeInventoryAdapter", side_effect=LiveDataError("HTTP 502")):
                rows, status = manager.search_all("콜라", 37.0, 127.0, ["이마트24"])
            self.assertEqual(status[0].source, "ERROR")
            self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
