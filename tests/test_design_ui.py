from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class DesignUiTests(unittest.TestCase):
    def app(self):return AppTest.from_file(str(APP),default_timeout=30).run()

    def test_first_screen_has_catalog_cart_and_no_engine_explanations(self):
        at=self.app();self.assertFalse(at.exception)
        self.assertEqual(at.session_state['workspace_view'],'상품 찾기')
        self.assertEqual(sum((b.key or '').startswith('catalog_add_') for b in at.button),12)
        self.assertEqual(sum((b.key or '').startswith('remove_') for b in at.button),3)
        self.assertFalse(at.info)
        text=' '.join(e.value for e in (*at.markdown,*at.caption))
        for technical in ('SQLite','동적 계획법','균형 점수 =','legacy_v211','보관 시간과 실제 조회'):
            self.assertNotIn(technical,text)

    def test_compare_navigation_keeps_search_and_quantities(self):
        at=self.app()
        at.number_input(key='qty_TEST-P003').set_value(3).run()
        at.text_input(key='search_keyword').set_value('커피');at.button(key='search_button').click().run()
        at.button(key='lookup_button').click().run()
        self.assertEqual(at.session_state['workspace_view'],'추천·경로')
        self.assertTrue(any(e.value=='방문 경로' for e in at.subheader))
        self.assertEqual(at.session_state['qty_TEST-P003'],3)
        at.button_group(key='workspace_view').set_value('상품 찾기').run()
        self.assertEqual(at.text_input(key='search_keyword').value,'커피')
        self.assertEqual(at.number_input(key='qty_TEST-P003').value,3)
        at.button_group(key='workspace_view').set_value('장바구니').run()
        self.assertEqual(at.number_input(key='qty_TEST-P003').value,3)
        self.assertTrue(at.session_state['search_result'].candidates);self.assertFalse(at.exception)

    def test_remove_product_invalidates_recommendation_and_can_add_again(self):
        at=self.app();at.button(key='remove_TEST-P003').click().run()
        self.assertNotIn('TEST-P003',{p.id for p in at.session_state['products']})
        self.assertIsNone(at.session_state['snapshot']);self.assertEqual(at.session_state['last_plan_count'],0)
        at.text_input(key='search_keyword').set_value('흰 우유 500ml');at.button(key='search_button').click().run()
        at.button(key='search_add_T:CU:TEST-P003').click().run()
        self.assertEqual(at.session_state['qty_TEST-P003'],1)
        self.assertEqual(len(at.session_state['links']['TEST-P003']),4);self.assertFalse(at.exception)

    def test_empty_search_and_cart_offer_recovery(self):
        at=self.app();at.button(key='clear_cart').click().run()
        self.assertTrue(at.button(key='lookup_button').disabled)
        at.text_input(key='search_keyword').set_value('일치하지않는검색어');at.button(key='search_button').click().run()
        self.assertTrue(any('찾지 못했습니다' in e.value for e in at.info))
        at.button(key='clear_search').click().run()
        self.assertEqual(at.text_input(key='search_keyword').value,'')
        self.assertEqual(sum((b.key or '').startswith('catalog_add_') for b in at.button),12)
        self.assertFalse(at.exception)

    def test_selected_mode_failure_is_visible_before_other_plans(self):
        at=self.app()
        for pid in ('TEST-P003','TEST-P027','TEST-P077'):at.number_input(key='qty_'+pid).set_value(6)
        at.number_input(key='budget').set_value(15000)
        at.checkbox(key='strict_budget').check()
        at.selectbox(key='mode').set_value('한 곳에서 구매').run()
        at.button(key='lookup_button').click().run()
        self.assertIsNone(at.session_state['last_summary']['한 곳에서 구매'])
        self.assertGreater(at.session_state['last_plan_count'],0)
        self.assertTrue(any('한 곳에서 구매' in e.value and '다른 구매안' in e.value for e in at.warning))
        self.assertFalse(at.exception)

    def test_view_switch_preserves_catalog_filters_and_selected_plan(self):
        at=self.app()
        at.selectbox(key='catalog_category').set_value('커피').run()
        at.selectbox(key='catalog_page').set_value(3).run()
        at.button(key='lookup_button').click().run()
        at.selectbox(key='plan_choice').select_index(2).run()
        selected=at.session_state['plan_choice']
        at.button_group(key='workspace_view').set_value('상품 찾기').run()
        self.assertEqual(at.selectbox(key='catalog_category').value,'커피')
        self.assertEqual(at.selectbox(key='catalog_page').value,3)
        at.button_group(key='workspace_view').set_value('추천·경로').run()
        self.assertEqual(at.selectbox(key='plan_choice').value,selected)
        self.assertFalse(at.exception)
