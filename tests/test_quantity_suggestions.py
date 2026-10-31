from dataclasses import replace
from datetime import datetime, timedelta
import unittest

from core.models import StoreProduct
from core.cart_models import CartItem
from core.services.cart_pricing import evaluate_line
from core.services.quantity_suggestions import suggest_quantity


class QuantitySuggestionTests(unittest.TestCase):
    def row(self, **kwargs):
        return replace(StoreProduct('CU', 'A', '서울', '음료', price=1200, promotion='2+1', stock=10), **kwargs)

    def suggestion(self, quantity, row=None):
        item=CartItem('drink', '음료', quantity)
        return suggest_quantity(item,evaluate_line(item,row or self.row()))

    def test_free_third_unit_and_paid_bundle_completion_are_distinguished(self):
        free=self.suggestion(2)
        self.assertEqual((free.suggested_quantity,free.original_total,free.suggested_total,free.extra_cost),(3,2400,2400,0))
        paid=self.suggestion(1)
        self.assertEqual((paid.suggested_quantity,paid.extra_cost),(3,1200))

    def test_one_plus_one_and_complete_bundles(self):
        self.assertEqual(self.suggestion(1,self.row(promotion='1+1')).extra_cost,0)
        self.assertIsNone(self.suggestion(2,self.row(promotion='1+1')))
        self.assertIsNone(self.suggestion(3))
        self.assertIsNone(self.suggestion(10))

    def test_unknown_short_and_unsupported_do_not_suggest(self):
        for change in ({'stock':2},{'stock_known':False},{'promotion_known':False},{'price':0},
                       {'promotion':'교차증정'},{'data_source':'PARTIAL'},{'stock_data_status':'CACHE'}):
            self.assertIsNone(self.suggestion(2,self.row(**change)),change)

    def test_live_price_and_promotion_expiry(self):
        now=datetime.now().astimezone().isoformat()
        old=(datetime.now().astimezone()-timedelta(hours=1)).isoformat()
        row=self.row(data_source='LIVE',price_data_status='LIVE',stock_data_status='LIVE',promotion_data_status='LIVE',
                     stock_updated_at=now,price_updated_at=now,promotion_updated_at=now)
        self.assertIsNotNone(self.suggestion(2,row))
        self.assertIsNone(self.suggestion(2,replace(row,price_updated_at=old)))
        self.assertIsNone(self.suggestion(2,replace(row,promotion_updated_at=old)))
