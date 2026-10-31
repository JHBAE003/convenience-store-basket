from dataclasses import replace
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core.models import StoreProduct,DataLoadResult

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class V2AppTests(unittest.TestCase):
    def app(self,scenario):
        at=AppTest.from_file(str(APP),default_timeout=30).run()
        self.addCleanup(lambda:at.session_state['temp_directory'].cleanup())
        at.radio(key='mode').set_value('TEST').run()
        at.selectbox(key='v2_demo_scenario').set_value(scenario).run()
        at.button(key='v2_load_scenario').click().run()
        self.assertEqual(len(at.exception),0)
        return at

    def test_budget_apply_preserves_locked_quantity_and_invalidates_old_result(self):
        at=self.app('예산 맞추기')
        self.assertEqual(at.number_input(key='budget').value,4000)
        self.assertTrue(at.checkbox(key='locked_noodle').value)
        at.button(key='v2_budget_apply_0').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(next(i.quantity for i in at.session_state['cart'] if i.item_id=='noodle'),1)
        self.assertIsNone(at.session_state['snapshot'])
        at.button(key='compare').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertLessEqual(at.session_state['recommendations'].cheapest.total,4000)

    def test_split_shows_both_single_and_two_store_totals(self):
        at=self.app('두 매장 비교')
        text='\n'.join(m.value for m in at.markdown)
        self.assertIn('4,900원',text)
        self.assertIn('5,300원',text)
        self.assertIn('방문 1곳 추가',text)
        self.assertEqual(len(at.session_state['cart']),3)

    def test_free_quantity_apply_clears_comparison_and_never_reapplies_stale_plan(self):
        at=self.app('행사 수량')
        self.assertEqual(at.session_state['cart'][0].quantity,1)
        at.button(key='v2_quantity_apply_drink').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.session_state['cart'][0].quantity,2)
        self.assertIsNone(at.session_state['snapshot'])
        at.button(key='compare').click().run()
        self.assertEqual(at.session_state['recommendations'].cheapest.total,5300)

    def test_demo_substitution_only_applies_after_explicit_click(self):
        at=self.app('품절 대체')
        old=at.session_state['cart'][0].label
        at.button(key='v2_sub_search').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.session_state['cart'][0].label,old)
        at.button(key='v2_sub_apply_0').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertIn('대체 음료',at.session_state['cart'][0].label)
        self.assertEqual(at.session_state['cart'][0].quantity,2)
        self.assertIsNone(at.session_state['snapshot'])
        at.button(key='compare').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertIsNotNone(at.session_state['recommendations'].cheapest)

    def test_all_required_items_can_make_budget_impossible(self):
        at=self.app('예산 맞추기')
        for item_id in ('drink','noodle','snack'):
            at.checkbox(key=f'locked_{item_id}').set_value(True).run()
        self.assertFalse(any(b.key and b.key.startswith('v2_budget_apply') for b in at.button))
        self.assertTrue(any('예산을 맞출 수 없습니다' in v.value for v in at.info))

    def test_expired_evidence_at_click_does_not_change_quantity(self):
        at=self.app('행사 수량')
        with patch('core.services.v2_common.current_evidence',return_value=False):
            at.button(key='v2_quantity_apply_drink').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(at.session_state['cart'][0].quantity,1)
        self.assertIsNone(at.session_state['snapshot'])
        self.assertTrue(any('유효 시간' in v.value for v in at.error))

    def test_live_alternative_metadata_requires_user_approval(self):
        now=datetime.now().astimezone().isoformat()
        common=dict(price=1200,promotion='1+1',stock=5,stock_quantity_exact=True,
                    latitude=37.458615,longitude=126.952661,data_source='LIVE',store_code='A',
                    price_source='모의 공식',promotion_source='모의 공식',stock_source='모의 공식',
                    price_updated_at=now,stock_updated_at=now,promotion_updated_at=now,
                    price_data_status='LIVE',stock_data_status='LIVE',promotion_data_status='LIVE')
        original=StoreProduct('CU','조회 매장','서울','원상품250ml',barcode='8801111111111',**common)
        alternative=StoreProduct('CU','조회 매장','서울','대체품250ml',barcode='8802222222222',**common)
        def search(keyword,*args,**kwargs):
            row=alternative if '대체품' in keyword else original
            return [row],[DataLoadResult('CU',[row],'LIVE','모의 조회 성공')]
        with patch('core.data.data_manager.DataManager.search_all',side_effect=search),\
             patch('core.ui.product_images.remote_photo',return_value=None):
            at=AppTest.from_file(str(APP),default_timeout=30).run()
            self.addCleanup(lambda:at.session_state['temp_directory'].cleanup())
            at.text_input(key='live_keyword').set_value('원상품').run()
            at.button(key='search_live').click().run()
            next(b for b in at.button if b.key and b.key.startswith('card_')).click().run()
            at.button(key='compare').click().run()
            at.pills(key='v2_tool').set_value('품절 대체').run()
            at.text_input(key='v2_sub_keyword').set_value('대체품').run()
            at.button(key='v2_sub_search').click().run()
            self.assertEqual(len(at.exception),0)
            self.assertFalse(any(b.key=='v2_sub_apply_0' for b in at.button))
            next(c for c in at.checkbox if c.key and c.key.startswith('v2_sub_approve_')).set_value(True).run()
            self.assertEqual(len(at.exception),0)
            at.button(key='v2_sub_apply_0').click().run()
            self.assertEqual(at.session_state['cart'][0].label,'대체품250ml')
            self.assertIsNone(at.session_state['snapshot'])
