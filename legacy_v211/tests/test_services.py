import unittest

from core.models import StoreProduct
from core.services.promotion import effective_unit_price
from core.services.recommendation import recommend
from core.services.search import search_products
from core.services.promotion import calculate_purchase_cost
from core.services.product_matching import same_product
from core.services.store_matching import same_store
from core.services.integration_service import integrate_records


class ServiceTests(unittest.TestCase):
    def test_promotion(self):
        self.assertEqual(effective_unit_price(1000, "1+1"), 500)
        self.assertEqual(effective_unit_price(1200, "2+1"), 800)
        self.assertEqual(effective_unit_price(900, ""), 900)

    def test_search_radius_and_brand(self):
        records = [
            StoreProduct("GS25", "A", "", "콜라", price=1000, stock=1, latitude=37.0, longitude=127.0),
            StoreProduct("CU", "B", "", "콜라", price=900, stock=1, latitude=38.0, longitude=128.0),
        ]
        result = search_products(records, "콜라", {"GS25"}, 37.0, 127.0, 1.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].brand, "GS25")

    def test_recommendation_ignores_sold_out(self):
        sold = StoreProduct("GS25", "A", "", "라면", price=500, stock=0, latitude=37.0, longitude=127.0)
        live = StoreProduct("CU", "B", "", "라면", price=700, promotion="1+1", stock=2, latitude=37.0, longitude=127.0)
        rec = recommend([sold, live], "최저가")
        self.assertIs(rec.item, live)

    def test_overall_recommendation_uses_regular_price_without_promotion(self):
        cheaper = StoreProduct("CU", "A", "", "초코바", price=1000, stock=5, distance_km=1.0)
        promotion = StoreProduct("GS25", "B", "", "초코바", price=2000,
                                 promotion="1+1", stock=5, distance_km=1.0)
        result = recommend([cheaper, promotion], "종합추천")
        self.assertIs(result.item, cheaper)
        self.assertIn("45%", result.reason)
        self.assertIn("35%", result.reason)
        self.assertIn("20%", result.reason)

    def test_bundle_purchase_cost_and_stock_limit(self):
        result = calculate_purchase_cost(1500, "2+1", 3, stock=3)
        self.assertEqual(result.payable, 3000)
        self.assertEqual(result.effective_unit_price, 1000)
        self.assertEqual(calculate_purchase_cost(1000, "10% 할인", 2).payable, 1800)
        self.assertEqual(calculate_purchase_cost(1000, "200원 할인", 2).payable, 1600)
        self.assertFalse(calculate_purchase_cost(1500, "2+1", 4, stock=3).known)
        self.assertFalse(calculate_purchase_cost(1500, "정률할인", 2).known)

    def test_product_and_store_matching_are_conservative(self):
        base = StoreProduct("CU", "점포A", "서울 관악구 1", "신라면", barcode="8801", store_code="C1")
        different_product = StoreProduct("CU", "점포A", "서울 관악구 1", "신라면 큰사발", barcode="8802", store_code="C1")
        same_product_other_store = StoreProduct("CU", "점포B", "서울 관악구 2", "신라면", barcode="8801", store_code="C2")
        self.assertFalse(same_product(base, different_product))
        self.assertFalse(same_store(base, same_product_other_store))

    def test_integration_does_not_join_another_store_price(self):
        stock = StoreProduct("CU", "점포A", "주소1", "콜라", barcode="8801", store_code="C1", stock=2)
        price = StoreProduct("CU", "점포B", "주소2", "콜라", barcode="8801", store_code="C2", price=1000, promotion="1+1")
        result = integrate_records([stock], [price])[0]
        self.assertEqual(result.price, 0)
        self.assertEqual(result.promotion, "")


if __name__ == "__main__":
    unittest.main()
