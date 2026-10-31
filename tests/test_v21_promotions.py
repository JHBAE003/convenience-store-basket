import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from core.cart_models import CartItem
from core.data.promotion_manager import PromotionManager
from core.models import StoreProduct
from core.scrapers.emart24_scraper import Emart24PromotionScraper
from core.scrapers.public_event_scraper import CuPromotionScraper
from core.scrapers.event_metadata import event_metadata
from core.services.cart_pricing import evaluate_line
from core.services.promotion import promotion_applicability, calculate_purchase_cost


def event(**changes):
    row = StoreProduct("CU", "점포", "서울", "콜라 500ml", price=1800, stock=5,
                       promotion="1+1", data_source="LIVE", promotion_known=True,
                       promotion_start_date="2026-10-01", promotion_end_date="2026-10-31",
                       promotion_buy_quantity=1, promotion_gift_quantity=1,
                       promotion_member_condition="없음", promotion_card_condition="없음",
                       promotion_cross_gift=False, promotion_conditions_verified=True)
    return replace(row, **changes)


class PeriodTests(unittest.TestCase):
    def test_month_end_is_inclusive_in_korean_time(self):
        # 14:59 UTC is 23:59 KST on the last day; one minute later is November.
        self.assertTrue(promotion_applicability(event(), datetime(2026, 10, 31, 14, 59, tzinfo=timezone.utc))[0])
        self.assertFalse(promotion_applicability(event(), datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc))[0])

    def test_start_boundary_uses_korean_date(self):
        row = event(promotion_start_date="2026-11-01", promotion_end_date="2026-11-30")
        self.assertFalse(promotion_applicability(row, datetime(2026, 10, 31, 14, 59, tzinfo=timezone.utc))[0])
        self.assertTrue(promotion_applicability(row, datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc))[0])

    def test_unknown_and_yearless_periods_are_not_invented(self):
        data = event_metadata("1+1", "2026-10-31T12:00:00+09:00", "공식 주소", period_text="10.01 ~ 10.31")
        self.assertEqual(data["promotion_start_date"], "")
        self.assertEqual(data["promotion_end_date"], "")
        self.assertEqual(data["raw_payload"]["period_text"], "10.01 ~ 10.31")
        self.assertFalse(promotion_applicability(event(promotion_end_date=""))[0])

    def test_full_source_dates_and_bundle_are_structured(self):
        data = event_metadata("2 + 1", "확인 시각", "공식 주소", period_text="2026.10.01 ~ 2026.10.31")
        self.assertEqual((data["promotion_start_date"], data["promotion_end_date"]), ("2026-10-01", "2026-10-31"))
        self.assertEqual((data["promotion_buy_quantity"], data["promotion_gift_quantity"]), (2, 1))
        self.assertEqual(data["promotion_member_condition"], "미확인")
        self.assertIsNone(data["promotion_cross_gift"])

    def test_conditional_or_unverified_events_never_enter_final_amount(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        for change in ({"promotion_member_condition": "회원전용"}, {"promotion_card_condition": "카드 결제"},
                       {"promotion_cross_gift": True}, {"promotion_cross_gift": None},
                       {"promotion_conditions_verified": False}, {"promotion_gift_quantity": None}):
            with self.subTest(change=change):
                self.assertFalse(promotion_applicability(event(**change), now)[0])
        self.assertFalse(calculate_purchase_cost(1800, "카드 결제 시 1+1", 2).known)

    def test_expired_cache_is_reference_at_calculation_time(self):
        row = event(promotion_start_date="2020-01-01", promotion_end_date="2020-01-31")
        result = evaluate_line(CartItem("cola", "콜라", 2), row)
        self.assertIsNone(result.payable)
        self.assertEqual(result.reference_payable, 3600)
        self.assertIn("종료", result.reason)

    def test_demo_and_explicit_regular_sales_keep_existing_calculation(self):
        self.assertTrue(promotion_applicability(event(data_source="TEST", promotion_start_date="", promotion_end_date=""))[0])
        self.assertTrue(promotion_applicability(event(promotion="", promotion_start_date="", promotion_end_date=""))[0])


class ManagerTests(unittest.TestCase):
    def test_catalog_singleflight_filters_each_waiter_own_keyword(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        class Scraper:
            def search(self, keyword):
                calls.append(keyword)
                entered.set()
                release.wait(2)
                return [event(product_name="콜라"), event(product_name="사이다")]

        with tempfile.TemporaryDirectory() as directory:
            manager = PromotionManager(Path(directory) / "events.json", {"CU": Scraper()})
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(manager.search, "CU", "콜라")
                self.assertTrue(entered.wait(1))
                second = pool.submit(manager.search, "CU", "사이다")
                time.sleep(.03)
                release.set()
                self.assertEqual([x.product_name for x in first.result()], ["콜라"])
                self.assertEqual([x.product_name for x in second.result()], ["사이다"])
            self.assertEqual(calls, [""])

    def test_force_bypasses_app_cache_and_preserves_missing_source_time(self):
        calls = []

        class Scraper:
            def search(self, keyword):
                calls.append(keyword)
                return [event(updated_at="2026-10-01T12:00:00+09:00", promotion_checked_at="2026-10-01T12:00:00+09:00")]

        with tempfile.TemporaryDirectory() as directory:
            manager = PromotionManager(Path(directory) / "events.json", {"CU": Scraper()})
            first = manager.search("CU", "콜라")[0]
            cached = manager.search("CU", "콜라")[0]
            forced = manager.search("CU", "콜라", force=True)[0]
            self.assertEqual(len(calls), 2)
            self.assertTrue(cached.app_cache_used)
            self.assertEqual(first.updated_at, cached.updated_at)
            self.assertEqual(cached.promotion_checked_at, first.promotion_checked_at)
            self.assertEqual(forced.source_updated_at, "")
            self.assertEqual(first.price_data_status, "REFERENCE")

    def test_empty_is_unknown_and_failure_has_bounded_cooldown(self):
        class Empty:
            def search(self, keyword):
                return []

        class Failure:
            calls = 0

            def search(self, keyword):
                self.calls += 1
                raise RuntimeError("비공개 응답 본문은 사용자 상태에 기록하지 않음")

        with tempfile.TemporaryDirectory() as directory:
            manager = PromotionManager(Path(directory) / "empty.json", {"CU": Empty()})
            self.assertEqual(manager.search("CU", "콜라"), [])
            self.assertEqual(manager.status_for("CU", "콜라")["status"], "UNKNOWN")
            failing = Failure()
            manager = PromotionManager(Path(directory) / "fail.json", {"CU": failing})
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    manager.search("CU", "콜라", force=True)
            self.assertEqual(failing.calls, 1)
            self.assertEqual(manager.status_for("CU", "콜라")["status"], "ERROR")
            self.assertEqual(manager.status_for("CU", "다른상품")["status"], "UNKNOWN")

    def test_expired_event_in_young_cache_cannot_become_current(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            row = event(promotion_end_date="2020-01-31", promotion_start_date="2020-01-01")
            from dataclasses import asdict
            path.write_text(json.dumps({"CU|*": {"saved_at": time.time(), "records": [asdict(row)]}}), encoding="utf-8")
            manager = PromotionManager(path, {"CU": None})
            # Restore guard remains safe independent of cache freshness.
            result = manager._restore(manager.cache["CU|*"])[0]
            self.assertFalse(result.promotion_known)
            self.assertEqual(result.updated_at, row.updated_at)


class EmartRequestTests(unittest.TestCase):
    def test_cu_reuses_public_ajax_without_redundant_landing_get(self):
        class Response:
            status_code = 200
            content = b'<li class="prod_list"><p class="name">test cola</p><strong class="price">1000</strong></li>'

            def raise_for_status(self):
                pass

        class Session:
            headers = {}

            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append(kwargs["data"])
                return Response()

            def get(self, *args, **kwargs):
                raise AssertionError("행사 AJAX 앞에 불필요한 화면 요청")

        session = Session()
        with patch("core.scrapers.public_event_scraper.time.sleep"):
            rows = CuPromotionScraper(session=session).search("cola")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual({row.promotion for row in rows}, {"1+1", "2+1"})
        self.assertTrue(all(row.price_data_status == "REFERENCE" for row in rows))

    def test_one_all_benefits_request_retains_source_period_without_year(self):
        class Response:
            status_code = 200
            text = '<div class="itemWrap"><div class="itemType"><span class="onepl">1 + 1</span></div>' \
                   '<div class="itemDate"><span>10.01 ~ 10.31</span></div>' \
                   '<div class="itemtitle"><a>콜라 500ml</a></div><a class="price">2,000원</a></div>'

            def raise_for_status(self):
                pass

        class Session:
            headers = {}
            calls = []

            def get(self, url, **kwargs):
                self.calls.append(kwargs)
                return Response()

        session = Session()
        row = Emart24PromotionScraper(session=session).search("콜라")[0]
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0]["params"]["category_seq"], 0)
        self.assertEqual(row.promotion, "1+1")
        self.assertEqual(row.raw_payload["period_text"], "10.01 ~ 10.31")
        self.assertEqual(row.promotion_end_date, "")
        self.assertEqual(row.price_data_status, "REFERENCE")
        self.assertFalse(row.promotion_conditions_verified)


if __name__ == "__main__":
    unittest.main()
