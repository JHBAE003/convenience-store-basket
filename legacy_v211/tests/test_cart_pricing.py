import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from core.models import StoreProduct
from core.cart_models import CartItem
from core.services.cart_pricing import evaluate_line


class CartPricingTests(unittest.TestCase):
    def setUp(self):
        self.row = StoreProduct('CU', 'A', '주소', '음료', price=1800,
                                promotion='1+1', stock=10, store_code='A')

    def test_bundle_quantities(self):
        for promo, paid in [('1+1', [1,1,2,2,3,3,4,4,5,5]),
                            ('2+1', [1,2,2,3,4,4,5,6,6,7]),
                            ('', list(range(1,11)))]:
            for q, expected in enumerate(paid, 1):
                with self.subTest(promo=promo, q=q):
                    result=evaluate_line(CartItem('drink','음료',q),replace(self.row,promotion=promo))
                    self.assertEqual(result.payable, expected*1800)

    def test_unknown_promotion_is_reference(self):
        r=evaluate_line(CartItem('drink','음료',2),replace(self.row,promotion_known=False))
        self.assertIsNone(r.payable)
        self.assertEqual(r.reference_payable,3600)

    def test_unknown_and_short_stock_are_distinct(self):
        item=CartItem('drink','음료',2)
        self.assertEqual(evaluate_line(item,replace(self.row,stock=1)).stock_state,'SHORT')
        self.assertEqual(evaluate_line(item,replace(self.row,stock_known=False)).stock_state,'UNKNOWN')
        self.assertEqual(evaluate_line(item,replace(self.row,stock_quantity_exact=False)).stock_state,'UNKNOWN')
        self.assertEqual(evaluate_line(item,None).stock_state,'UNKNOWN')

    def test_missing_price_and_unsupported_offer(self):
        item=CartItem('drink','음료',2)
        self.assertEqual(evaluate_line(item,replace(self.row,price=0)).pricing_state,'UNKNOWN')
        self.assertEqual(evaluate_line(item,replace(self.row,promotion='3+1')).pricing_state,'REFERENCE')

    def test_quantity_limits(self):
        for q in [0,11,-1,True,1.5]:
            with self.assertRaises(ValueError): CartItem('x','상품',q)

    def test_old_live_observation_is_not_current_stock(self):
        old=(datetime.now().astimezone()-timedelta(minutes=10)).isoformat()
        r=evaluate_line(CartItem('d','음료',2),replace(self.row,data_source='LIVE',stock_updated_at=old))
        self.assertEqual(r.stock_state,'UNKNOWN')
