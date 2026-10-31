import unittest
from dataclasses import replace
from core.models import StoreProduct
from core.cart_models import CartItem, CatalogLink
from core.services.catalog import resolve_cart_records
from core.data.csv_adapter import CsvAdapter
from core.config import TEST_DATA_PATH


class CatalogTests(unittest.TestCase):
    def test_explicit_cross_brand_links_and_wrong_code(self):
        links=[CatalogLink('drink','CU','8801','','음료 250ml'),
               CatalogLink('drink','GS25','8801','','음료 250ml')]
        a=StoreProduct('CU','A','주소','음료 250ml',barcode='8801')
        b=replace(a,brand='GS25')
        wrong=replace(a,barcode='8802',product_name='음료 500ml')
        result=resolve_cart_records([CartItem('drink','음료',2)],[a,b,wrong],links)
        self.assertEqual(result['drink'],[a,b])

    def test_no_link_does_not_guess_names(self):
        row=StoreProduct('CU','A','주소','음료',barcode='8801')
        self.assertEqual(resolve_cart_records([CartItem('x','음료',2)],[row],[])['x'],[])

    def test_demo_csv_preserves_unknown_state(self):
        rows=CsvAdapter(TEST_DATA_PATH).search('',37.458115,126.952161)
        self.assertTrue(any(not x.stock_known for x in rows))
        self.assertTrue(any(not x.promotion_known for x in rows))
        self.assertTrue(any(x.price==0 for x in rows))
