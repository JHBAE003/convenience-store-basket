import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'

class UiTests(unittest.TestCase):
    def test_database_default_all_stores_and_no_scenario_picker(self):
        at=AppTest.from_file(str(APP),default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],4100)
        self.assertEqual(len(at.session_state['snapshot'].stores),24)
        self.assertFalse(any((b.key or '').startswith('scenario_') for b in at.button))
        self.assertFalse(any('8가지' in e.label for e in at.expander))
        at.selectbox(key='mode').set_value('최단 이동').run()
        self.assertFalse(at.exception)
        self.assertGreater(at.session_state['last_plan_count'],0)

    def test_budget_constraints_recompute_from_same_database(self):
        at=AppTest.from_file(str(APP),default_timeout=30).run()
        at.number_input(key='budget').set_value(0).run()
        at.checkbox(key='strict_budget').check().run()
        self.assertEqual(at.session_state['last_plan_count'],0)
        at.number_input(key='budget').set_value(10000).run()
        self.assertGreater(at.session_state['last_plan_count'],0)
        self.assertFalse(at.exception)

    def test_extra_gift_is_explicit_and_clear_cart(self):
        at=AppTest.from_file(str(APP),default_timeout=30).run()
        at.button_group(key='workspace_view').set_value('추천·경로').run()
        options=[b for b in at.button if (b.key or '').startswith('extra_')]
        self.assertTrue(options)
        before=sum(at.session_state['qty_'+p.id] for p in at.session_state['products'])
        options[0].click().run()
        after=sum(at.session_state['qty_'+p.id] for p in at.session_state['products'])
        self.assertGreater(after,before)
        at.button(key='clear_cart').click().run()
        self.assertEqual(at.session_state['last_plan_count'],0)
        self.assertEqual(at.session_state['products'],[])
        self.assertFalse(at.exception)

if __name__=='__main__':unittest.main()
