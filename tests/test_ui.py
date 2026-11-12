import unittest
from pathlib import Path
from streamlit.testing.v1 import AppTest
from planner.test_data import TestDataset

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'

class UiTests(unittest.TestCase):
    def test_database_default_all_stores_and_no_scenario_picker(self):
        at=AppTest.from_file(str(APP),default_timeout=30).run()
        self.assertFalse(at.exception)
        data=TestDataset()
        with data.connect() as db:
            lower=sum(db.execute('SELECT min(price) FROM offers WHERE product_id=? AND stock>=1',(pid,)).fetchone()[0]
                      for pid in data.metadata()['default_products'])
        self.assertEqual(at.session_state['last_summary']['최저 결제액']['total'],lower)
        self.assertEqual([p.id for p in at.session_state['products']],data.metadata()['default_products'])
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
        at.number_input(key='budget').set_value(1000000).run()
        self.assertGreater(at.session_state['last_plan_count'],0)
        self.assertFalse(at.exception)

    def test_extra_gift_is_explicit_and_clear_cart(self):
        at=AppTest.from_file(str(APP),default_timeout=30).run()
        # 실제로 저장된 행사 상품을 골라, 기본 장바구니 구성에 의존하지 않는다.
        data=TestDataset();as_of=data.metadata()['fixed_date']
        with data.connect() as db:
            item=db.execute('''SELECT p.id,p.name FROM products p JOIN offers o ON o.product_id=p.id
              WHERE o.promotion IN ('1+1','2+1') AND o.stock>=3 AND o.conditions_verified=1
              AND o.start<=? AND o.end>=? ORDER BY p.id LIMIT 1''',(as_of,as_of)).fetchone()
        self.assertIsNotNone(item)
        at.button(key='clear_cart').click().run()
        at.text_input(key='search_keyword').set_value(item['name']);at.button(key='search_button').click().run()
        at.button(key='search_add_T:CU:'+item['id']).click().run()
        at.button(key='lookup_button').click().run()
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
