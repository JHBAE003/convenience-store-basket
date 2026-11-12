import unittest
import tempfile
from pathlib import Path

from core.data.promotion_manager import PromotionManager
from core.scrapers.emart24_scraper import Emart24PromotionScraper
from core.scrapers.public_event_scraper import SevenElevenPromotionScraper
from core.models import StoreProduct
from core.services.integration_service import integrate_records
from core.services.product_matching import same_product


class FakeResponse:
    status_code = 200
    text = '''<section class="itemList"><div class="itemWrap">
      <div class="itemTit"><span class="sale floatR">세일</span></div>
      <div class="itemSpImg"><img src="https://msave.emart24.co.kr/plu_image/8801234567890.JPG"></div>
      <div class="itemtitle"><p><a>테스트 콜라 500ml</a></p></div>
      <a class="priceOff">2,000 원</a><a class="price">1,800 원</a>
    </div></section>'''

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.headers = {}

    def get(self, *args, **kwargs):
        return FakeResponse()


class PromotionTests(unittest.TestCase):
    def test_unknown_promotion_uses_regular_price_as_effective_price(self):
        item = StoreProduct("CU", "CU점", "주소", "콜라", price=1500,
                            promotion_known=False)
        self.assertEqual(item.promotion, "")
        self.assertEqual(item.promotion_price_display, "1,500원")

    def test_emart_official_event_html_extracts_sku_price_and_event(self):
        rows = Emart24PromotionScraper(session=FakeSession()).search("콜라")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].product_code, "8801234567890")
        self.assertEqual(rows[0].price, 1800)
        self.assertEqual(rows[0].promotion, "세일")

    def test_seven_official_ajax_is_queried_without_landing_page(self):
        class Response:
            status_code = 200

            def __init__(self, content):
                self.content = content.encode("utf-8")

            def raise_for_status(self):
                pass

        class Session:
            def __init__(self):
                self.headers = {}
                self.calls = []

            def post(self, url, data, headers, timeout):
                self.calls.append((url, data["intCurrPage"]))
                if data["intCurrPage"] == "1":
                    return Response('<div class="pic_product"><img src="/img/a.jpg">'
                                    '<div class="name">테스트 콜라 500ml</div>'
                                    '<div class="price">2,000원</div></div>')
                return Response("")

        session = Session()
        scraper = SevenElevenPromotionScraper(session=session)
        scraper.TABS = ((1, "1+1"),)
        rows = scraper.search("콜라")
        self.assertEqual([(row.product_name, row.price, row.promotion) for row in rows],
                         [("테스트 콜라 500ml", 2000, "1+1")])
        self.assertEqual([page for _, page in session.calls], ["1", "2"])

    def test_brand_level_price_joins_by_product_code_not_store_name(self):
        inventory = StoreProduct("이마트24", "이마트24점", "서울 주소", "테스트 콜라 500ml",
                                 product_code="8801234567890", store_code="E1", stock=3,
                                 price=0, promotion_known=False)
        price = StoreProduct("이마트24", "", "", "테스트 콜라 500ml",
                             product_code="8801234567890", price=1800, promotion="세일",
                             price_source="공식 행사 페이지", promotion_known=True,
                             data_source="LIVE")
        result = integrate_records([inventory], [price])[0]
        self.assertEqual(result.price, 1800)
        self.assertEqual(result.promotion, "세일")
        self.assertEqual(result.price_source, "공식 행사 페이지")

    def test_same_barcode_from_another_brand_does_not_join(self):
        stock = StoreProduct("CU", "CU점", "서울", "콜라", barcode="8801234567890")
        event = StoreProduct("GS25", "", "", "콜라", barcode="8801234567890",
                             price=2000, promotion="1+1")
        self.assertFalse(same_product(stock, event))
        self.assertEqual(integrate_records([stock], [event])[0].promotion, "")

    def test_duplicate_event_cards_merge_and_best_offer_is_selected(self):
        stock = StoreProduct("CU", "CU점", "서울", "콜라 500ml", product_code="8801234567890",
                             price=2000, promotion_known=False)
        one_plus_one = StoreProduct("CU", "", "", "콜라500ml", barcode="8801234567890",
                                    product_code="8801234567890", price=2000, promotion="1+1", data_source="TEST")
        two_plus_one = StoreProduct("CU", "", "", "콜라500ml", barcode="8801234567890",
                                    product_code="8801234567890", price=2000, promotion="2+1", data_source="TEST")
        joined = integrate_records([stock], [one_plus_one, one_plus_one, two_plus_one])[0]
        self.assertEqual(joined.promotion, "1+1")
        self.assertEqual(joined.promotion_price_display, "1,000원")

    def test_conflicting_event_prices_do_not_overwrite_inventory(self):
        stock = StoreProduct("CU", "CU점", "서울", "콜라", product_code="8801234567890",
                             price=2000, promotion_known=False)
        events = [StoreProduct("CU", "", "", "콜라", product_code="8801234567890",
                               price=price, promotion="1+1", data_source="LIVE")
                  for price in (2000, 2200)]
        joined = integrate_records([stock], events)[0]
        self.assertEqual(joined.price, 2000)
        self.assertFalse(joined.promotion_known)

    def test_existing_store_event_takes_priority_over_catalog(self):
        stock = StoreProduct("CU", "CU점", "서울", "콜라", product_code="8801234567890",
                             price=2000, promotion="2+1", promotion_known=True,
                             promotion_source="재고 응답")
        event = StoreProduct("CU", "", "", "콜라", product_code="8801234567890",
                             price=2000, promotion="1+1", data_source="LIVE")
        joined = integrate_records([stock], [event])[0]
        self.assertEqual(joined.promotion, "2+1")
        self.assertEqual(joined.promotion_source, "재고 응답")

    def test_cu_catalog_is_reused_for_different_keywords_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []

            class Scraper:
                def search(self, keyword):
                    calls.append(keyword)
                    return [
                        StoreProduct("CU", "", "", "테스트 콜라", price=1000, promotion="1+1", data_source="LIVE"),
                        StoreProduct("CU", "", "", "테스트 사이다", price=1200, promotion="2+1", data_source="LIVE"),
                    ]

            path = Path(folder) / "promotions.json"
            manager = PromotionManager(cache_path=path, scraper={"CU": Scraper()})
            first = manager.search("CU", "콜라")
            restored = PromotionManager(cache_path=path, scraper={"CU": Scraper()})
            second = restored.search("CU", "사이다")
            self.assertEqual(calls, [""])
            self.assertEqual([row.product_name for row in first], ["테스트 콜라"])
            self.assertEqual([row.product_name for row in second], ["테스트 사이다"])
            self.assertEqual(first[0].data_source, "LIVE")
            self.assertEqual(second[0].data_source, "CACHE")


if __name__ == "__main__":
    unittest.main()
