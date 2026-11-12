import unittest
import time
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from test_ui import APP
from test_providers import FakeManager
from planner.providers import LiveProvider


def add_first_result(at):
    next(b for b in at.button if (b.key or '').startswith('search_add_')).click().run()


class ModeUiTests(unittest.TestCase):
    def app(self):return AppTest.from_file(str(APP),default_timeout=20).run()

    def test_switch_clears_cart_results_and_does_not_initialize_live(self):
        with patch.object(LiveProvider,'manager',side_effect=AssertionError('자동 조회 금지')) as create:
            at=self.app()
            self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],4100)
            before=[h.value for h in at.subheader]
            at.radio(key='data_mode').set_value('LIVE').run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['products'],[])
            self.assertEqual(at.session_state['search_keyword'],'')
            self.assertEqual(at.session_state['last_plan_count'],0)
            # 구매 목록/지도는 두 모드 모두 결과가 있을 때 공통 코드에서 표시한다.
            self.assertEqual(before[:5],[h.value for h in at.subheader][:5])
            create.assert_not_called()
            at.radio(key='data_mode').set_value('TEST').run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['qty_TEST-P003'],1)
            self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],4100)

    def test_same_search_cart_lookup_and_recommendation_flow(self):
        manager=FakeManager()
        with patch.object(LiveProvider,'manager',return_value=manager):
            at=self.app()
            at.button(key='clear_cart').click().run()
            at.text_input(key='search_keyword').set_value('우유')
            at.button(key='search_button').click().run()
            add_first_result(at)
            at.number_input(key='qty_TEST-P001').set_value(2).run()
            at.button(key='lookup_button').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],1200)
            self.assertEqual(len(at.session_state['snapshot'].stores),24)
            sections=[h.value for h in at.subheader]
            at.radio(key='data_mode').set_value('LIVE').run()
            at.text_input(key='search_keyword').set_value('우유')
            at.button(key='search_button').click().run()
            self.assertEqual(len(manager.calls),1)
            add_first_result(at)
            pid=at.session_state['products'][0].id
            at.number_input(key='qty_'+pid).set_value(2).run()
            self.assertEqual(len(manager.calls),1)  # 담기/수량 변경은 외부 요청 아님
            at.button(key='lookup_button').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],2400)
            self.assertEqual(sections,[h.value for h in at.subheader])
            self.assertEqual(manager.calls[-1][0],'selected')
            count=len(manager.calls)
            at.selectbox(key='mode').set_value('최단 이동').run()
            self.assertEqual(len(manager.calls),count)
            at.number_input(key='origin_lat').set_value(37.55).run()
            self.assertEqual(at.session_state['last_plan_count'],0)
            self.assertEqual(len(manager.calls),count)

    def test_live_failure_remains_live_and_no_fake_recommendation(self):
        manager=FakeManager()
        with patch.object(LiveProvider,'manager',return_value=manager):
            at=self.app();at.radio(key='data_mode').set_value('LIVE').run()
            at.text_input(key='search_keyword').set_value('우유')
            at.button(key='search_button').click().run()
            add_first_result(at)
            manager.rows=[]
            at.button(key='lookup_button').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['data_mode'],'LIVE')
            self.assertEqual(at.session_state['last_plan_count'],0)
            self.assertNotIn('qty_TEST-P003',at.session_state)

    def test_live_expiry_and_force_refresh_are_explicit(self):
        manager=FakeManager()
        with patch.object(LiveProvider,'manager',return_value=manager):
            at=self.app();at.radio(key='data_mode').set_value('LIVE').run()
            at.text_input(key='search_keyword').set_value('우유')
            at.button(key='search_button').click().run()
            add_first_result(at)
            at.button(key='lookup_button').click().run()
            self.assertGreater(at.session_state['last_plan_count'],0)
            count=len(manager.calls)
            at.session_state['snapshot'].loaded_at=time.monotonic()-61
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['last_plan_count'],0)
            self.assertEqual(len(manager.calls),count)
            at.checkbox(key='force_refresh').check().run()
            self.assertEqual(len(manager.calls),count)
            at.button(key='lookup_button').click().run()
            self.assertTrue(manager.calls[-1][-1]['force_refresh'])
            self.assertGreater(at.session_state['last_plan_count'],0)


if __name__=='__main__':unittest.main()
