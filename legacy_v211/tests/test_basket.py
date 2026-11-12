import unittest
from dataclasses import replace
from core.cart_models import CartItem
from core.models import StoreProduct
from core.services.basket import evaluate_stores
from core.services.basket_recommendation import recommend_baskets
from core.services.catalog import load_catalog, resolve_cart_records
from core.data.csv_adapter import CsvAdapter
from core.config import TEST_DATA_PATH


class BasketTests(unittest.TestCase):
    def setUp(self):
        self.cart=[CartItem('drink','음료',2),CartItem('noodle','컵라면',1),CartItem('snack','과자',1)]
        self.rows=CsvAdapter(TEST_DATA_PATH).search('',37.458115,126.952161)
        self.links=load_catalog()

    def evaluate(self, rows=None):
        return evaluate_stores(self.cart,resolve_cart_records(self.cart,self.rows if rows is None else rows,self.links),
                               37.458115,126.952161,2)

    def test_documented_basket_and_budget(self):
        results=self.evaluate()
        indexed={r.store_key:r for r in results}
        self.assertEqual(indexed['CU|A'].total,5300)
        self.assertEqual(indexed['GS25|B'].total,6500)
        self.assertFalse(indexed['세븐일레븐|C'].eligible)
        rec=recommend_baskets(results,6000)
        self.assertEqual(rec.cheapest.store_key,'CU|A')
        self.assertIsNone(rec.over_budget_by)

    def test_budget_none_and_empty(self):
        results=self.evaluate()
        self.assertEqual(recommend_baskets(results,5000).over_budget_by,300)
        self.assertIsNotNone(recommend_baskets(results,0).cheapest)
        self.assertIsNone(recommend_baskets([],6000).cheapest)

    def test_cache_partial_and_unknown_price_not_recommended(self):
        for status in ['CACHE','PARTIAL','ERROR']:
            results=self.evaluate([replace(x,data_source=status) for x in self.rows])
            self.assertFalse(any(x.eligible for x in results))

    def test_conflicting_duplicate_not_silently_selected(self):
        base=next(x for x in self.rows if x.store_code=='A')
        results=self.evaluate(self.rows+[replace(base,price=base.price+100)])
        self.assertFalse(next(x for x in results if x.store_key=='CU|A').eligible)

    def test_unknown_distance_and_zero_distance(self):
        unknown=[replace(x,latitude=None,longitude=None) for x in self.rows]
        self.assertFalse(any(x.eligible for x in self.evaluate(unknown)))
        at_location=[replace(x,latitude=37.458115,longitude=126.952161) for x in self.rows]
        self.assertEqual(recommend_baskets(self.evaluate(at_location),0).nearest.distance_km,0)
