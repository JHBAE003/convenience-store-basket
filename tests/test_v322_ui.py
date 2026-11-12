"""총거리 추천·선택 표시·구간표의 일관성을 TEST에서 확인한다."""
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class TotalRouteUiTests(unittest.TestCase):
    def test_mode_change_selects_the_matching_purchase_plan(self):
        at=AppTest.from_file(str(APP),default_timeout=45).run()
        self.assertEqual(at.session_state['mode'],'가격·거리 균형')
        at.button(key='lookup_button').click().run()
        balanced=at.selectbox(key='plan_choice').value
        at.selectbox(key='mode').set_value('최저 결제액').run()
        cheapest=at.session_state['last_summary']['최저 결제액']
        selected=at.selectbox(key='plan_choice').value
        self.assertEqual({line[0] for line in selected},set(cheapest['route']))
        at.selectbox(key='mode').set_value('가격·거리 균형').run()
        self.assertEqual(at.selectbox(key='plan_choice').value,balanced)
        self.assertFalse(at.exception)

    def test_each_walking_leg_accumulates_to_displayed_total(self):
        at=AppTest.from_file(str(APP),default_timeout=45).run()
        at.selectbox(key='mode').set_value('최저 결제액').run()
        at.button(key='lookup_button').click().run()
        plan=at.session_state['last_summary']['최저 결제액']
        legs=at.session_state['last_route_legs']
        self.assertEqual(len(legs),len(plan['route']))
        self.assertEqual(legs[-1]['누적 거리 (m)'],round(plan['distance']*1000))
        self.assertTrue(all(a['누적 거리 (m)']<=b['누적 거리 (m)'] for a,b in zip(legs,legs[1:])))
        received=next(e for e in at.expander if e.label=='상품·행사 참고 자료')
        self.assertFalse(received.proto.expanded)
        self.assertFalse(at.exception)
