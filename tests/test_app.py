import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from unittest.mock import patch
from dataclasses import replace
from datetime import datetime
from core.models import StoreProduct, DataLoadResult

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class AppTests(unittest.TestCase):
    def app(self):
        at=AppTest.from_file(str(APP),default_timeout=20).run()
        at.radio(key='mode').set_value('TEST').run()
        self.assertEqual(len(at.exception),0)
        self.addCleanup(lambda:at.session_state['temp_directory'].cleanup())
        return at

    def test_demo_end_to_end_and_budget_reevaluation(self):
        at=self.app()
        at.button(key='load_demo').click().run()
        self.assertEqual(len(at.session_state['cart']),3)
        at.button(key='compare').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.session_state['recommendations'].cheapest.total,5300)
        at.number_input(key='budget').set_value(5000).run()
        self.assertEqual(at.session_state['recommendations'].over_budget_by,300)

    def test_quantity_change_invalidates_snapshot(self):
        at=self.app();at.button(key='load_demo').click().run()
        at.button(key='compare').click().run()
        at.number_input(key='qty_drink').set_value(3).run()
        self.assertIsNone(at.session_state['snapshot'])
        self.assertEqual(at.session_state['cart'][0].quantity,3)

    def test_distinct_sessions_and_remove(self):
        a=self.app();b=self.app()
        a.button(key='load_demo').click().run()
        self.assertEqual(b.session_state['cart'],[])
        self.assertNotEqual(a.session_state['manager'].cache_path,b.session_state['manager'].cache_path)
        a.button(key='remove_drink').click().run()
        self.assertEqual(len(a.session_state['cart']),2)

    def test_live_mode_does_not_use_demo_cart_or_call_network(self):
        at=self.app();at.button(key='load_demo').click().run()
        at.radio(key='mode').set_value('LIVE').run()
        self.assertEqual(at.session_state['cart'],[])
        self.assertEqual(at.session_state['links'],[])
        self.assertIsNone(at.session_state['snapshot'])

    def test_empty_quantity_is_not_a_crash_or_stale_comparison(self):
        at=self.app();at.button(key='load_demo').click().run()
        at.button(key='compare').click().run()
        at.number_input(key='qty_drink').set_value(None).run()
        self.assertEqual(len(at.exception),0)
        self.assertTrue(at.button(key='compare').disabled)
        self.assertIsNone(at.session_state['snapshot'])

    def test_live_selection_and_comparison_with_mocked_provider(self):
        row=StoreProduct('CU','실시간 모의 매장','서울 관악구','검증용 음료',barcode='TEST8801',price=1800,
                         stock=5,promotion='1+1',store_code='L1',data_source='LIVE',latitude=37.4582,longitude=126.9522,
                         stock_updated_at=datetime.now().astimezone().isoformat(),
                         price_updated_at=datetime.now().astimezone().isoformat(),
                         promotion_updated_at=datetime.now().astimezone().isoformat(),
                         price_source='모의 제공자',stock_source='모의 제공자',promotion_source='모의 제공자',
                         price_data_status='LIVE',stock_data_status='LIVE',promotion_data_status='LIVE')
        with patch('core.data.data_manager.DataManager.search_all',return_value=([row],[DataLoadResult('CU',[row],'LIVE','모의 제공자')])):
            at=self.app();at.radio(key='mode').set_value('LIVE').run()
            at.text_input(key='live_keyword').set_value('검증용 음료')
            next(b for b in at.button if b.label=='실시간 상품 검색').click().run()
            at.multiselect(key='live_selection').set_value([('CU','TEST8801','','검증용 음료')])
            next(b for b in at.button if b.label=='선택 상품 담기').click().run()
            self.assertEqual(len(at.exception),0)
            self.assertEqual(len(at.session_state['cart']),1)
            at.button(key='compare').click().run()
            self.assertEqual(len(at.exception),0)
            self.assertEqual(at.session_state['recommendations'].cheapest.total,1800)
