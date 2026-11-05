import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'

class UiTests(unittest.TestCase):
    def test_scenarios_and_modes_recompute(self):
        at=AppTest.from_file(str(APP),default_timeout=20).run()
        self.assertFalse(at.exception)
        for id in range(1,9):
            at.button(key=f'scenario_{id}').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['scenario_id'],id)
        at.button(key='scenario_7').click().run()
        at.selectbox(key='mode').set_value('최단 이동').run()
        self.assertEqual(at.session_state['last_summary']['최단 이동']['route'],('A',))
        at.selectbox(key='mode').set_value('최저 결제액').run()
        self.assertEqual(at.session_state['last_summary']['최저 결제액']['route'],('B',))
        at.number_input(key='qty_milk').set_value(1).run()
        self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],700)

    def test_three_stores_failure_and_recovery(self):
        at=AppTest.from_file(str(APP),default_timeout=20).run()
        at.button(key='scenario_4').click().run()
        at.slider(key='max_stores').set_value(2).run()
        self.assertEqual(at.session_state['last_plan_count'],0)
        self.assertTrue(at.warning)
        at.slider(key='max_stores').set_value(3).run()
        self.assertGreater(at.session_state['last_plan_count'],0)
        self.assertFalse(at.exception)

    def test_extra_gift_is_explicit_and_empty_cart(self):
        at=AppTest.from_file(str(APP),default_timeout=20).run()
        at.button(key='scenario_6').click().run()
        self.assertEqual(at.session_state['qty_milk'],3)
        at.button(key='extra_A_milk').click().run()
        self.assertEqual(at.session_state['qty_milk'],4)
        at.number_input(key='qty_milk').set_value(0).run()
        self.assertEqual(at.session_state['last_plan_count'],0)
        self.assertFalse(at.exception)

if __name__=='__main__':unittest.main()
