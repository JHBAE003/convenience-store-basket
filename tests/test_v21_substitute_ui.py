"""대체 상품 검색은 목록 조회, 선택 상품 재고 확인의 두 단계로 진행한다."""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
import unittest

from streamlit.testing.v1 import AppTest

from core.cart_models import CartItem
from core.models import DataLoadResult, StoreProduct
from core.services.catalog import catalog_link_for
from core.services.product_list import product_key
from tests.live_fixtures import verified_offer


class PanelManager:
    def __init__(self):
        observed = datetime.now().astimezone().isoformat()
        common = dict(price=1200, promotion='1+1', stock=5, store_code='S1',
                      latitude=37.458615, longitude=126.952661, data_source='LIVE',
                      updated_at=observed, price_updated_at=observed, stock_updated_at=observed,
                      stock_source_updated_at=observed, promotion_updated_at=observed,
                      price_source='모의 공식 자료', stock_source='모의 공식 자료', promotion_source='모의 공식 자료',
                      price_data_status='LIVE', stock_data_status='LIVE', promotion_data_status='LIVE',
                      **verified_offer())
        self.original = StoreProduct('CU', '검증점', '서울', '원상품250ml',
                                     barcode='ORIGINAL', product_code='P0', **{**common, 'stock': 1})
        self.actual = [StoreProduct('CU', '검증점', '서울', f'대체품{letter}250ml',
                                   barcode=f'ALT-{letter}', product_code=f'P-{letter}', **common)
                       for letter in ('A', 'B')]
        self.catalog = [replace(item, store_name='', address='', store_code='',
                                latitude=None, longitude=None, price=99999, stock=0,
                                stock_known=False, promotion_known=False)
                        for item in self.actual]
        self.catalog_calls = []
        self.inventory_calls = []
        self.case = 'success'

    def search_products(self, keyword, lat, lng, brands, mode):
        self.catalog_calls.append((keyword, lat, lng, tuple(brands), mode))
        return list(self.catalog), [DataLoadResult('CU', list(self.catalog), 'LIVE', '모의 상품 목록 확인')]

    def search_all(self, *args, **kwargs):
        raise AssertionError('대체 상품 목록에서 넓은 재고 검색을 호출하면 안 됩니다.')

    def search_selected(self, link, lat, lng, *, mode, force_refresh):
        self.inventory_calls.append((link, lat, lng, mode, force_refresh))
        if self.case == 'error':
            raise RuntimeError('모의 오류')
        selected = next(item for item in self.actual if item.product_code == link.product_code)
        rows, source = [selected], 'LIVE'
        if self.case == 'partial':
            source = 'PARTIAL'
        elif self.case == 'cached':
            source = 'CACHE'
        elif self.case == 'zero':
            rows = [replace(selected, stock=0)]
        elif self.case == 'missing_time':
            rows = [replace(selected, stock_updated_at='', updated_at='', stock_source_updated_at='')]
        elif self.case == 'unknown_source':
            rows = [replace(selected, data_source='UNKNOWN')]
        elif self.case == 'outside_radius':
            rows = [replace(selected, latitude=38.5)]
        elif self.case == 'original_conflict':
            rows += [replace(self.original, price=1000, product_name='원상품 별칭250ml')]
        statuses=[DataLoadResult('CU', rows, source, '모의 선택 재고 확인')]
        if self.case == 'missing_status':
            statuses=[]
        elif self.case == 'mixed_status':
            statuses.insert(0, DataLoadResult('CU', [], 'ERROR', '일부 조회 실패'))
        elif self.case == 'test_in_live':
            statuses=[DataLoadResult('CU', rows, 'TEST', '잘못 반환된 시연 자료')]
            rows=[replace(item,data_source='TEST') for item in rows]
        return rows, statuses


def render_fixture():
    import streamlit as st
    from core.ui.v2_panels import _substitutes
    from core.ui.v2_actions import comparison_context
    if 'manager' not in st.session_state:
        manager = PanelManager()
        st.session_state.update(manager=manager, cart=[CartItem('drink', manager.original.product_name, 2)],
                                mode='LIVE', latitude=37.458115, longitude=126.952161,
                                radius=2.0, budget=6000, brands=['CU'],
                                links=[catalog_link_for(manager.original, 'drink')],
                                snapshot=SimpleNamespace(completed_at=manager.original.updated_at))
    original = st.session_state.manager.original
    stores = [SimpleNamespace(store_key='CU:S1', store_name='검증점',
                              lines=[SimpleNamespace(item_id='drink', record=original, pricing_state='CONFIRMED')])]
    _substitutes(stores, st.session_state.cart, st.session_state.latitude, st.session_state.longitude,
                 st.session_state.radius, st.session_state.brands, st.session_state.mode, comparison_context())


class SelectedSubstituteUiTests(unittest.TestCase):
    def app(self):
        at = AppTest.from_string('from tests.test_v21_substitute_ui import render_fixture\nrender_fixture()',
                                 default_timeout=20).run()
        self.assertEqual(len(at.exception), 0)
        return at

    def search(self, at):
        at.text_input(key='v2_sub_keyword').set_value('대체품').run()
        at.button(key='v2_sub_search').click().run()
        self.assertEqual(len(at.exception), 0)

    @staticmethod
    def approve(at):
        next(box for box in at.checkbox if box.key and box.key.startswith('v2_sub_approve_')).set_value(True).run()

    @staticmethod
    def applies(at):
        return any(button.key and button.key.startswith('v2_sub_apply_') for button in at.button)

    def test_list_search_never_queries_inventory_and_explicit_check_uses_selected_code(self):
        at = self.app()
        self.search(at)
        manager = at.session_state['manager']
        self.assertEqual(len(manager.catalog_calls), 1)
        self.assertEqual(manager.inventory_calls, [])
        self.assertFalse(self.applies(at))
        self.assertEqual(len(at.session_state['v2_alt_rows']), 2)  # 좌표 없는 목록도 유지
        self.approve(at)
        self.assertFalse(self.applies(at))
        at.button(key='v21_sub_check_stock').click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(manager.inventory_calls), 1)
        self.assertEqual(manager.inventory_calls[0][0].product_code, 'P-A')
        self.assertEqual(manager.inventory_calls[0][-1], False)
        self.assertTrue(self.applies(at))
        self.assertTrue(any('1,200원' in element.value for element in at.markdown))
        self.assertFalse(any('99,999원' in element.value for element in at.markdown))

    def test_changed_candidate_or_metadata_cannot_reuse_previous_stock(self):
        at = self.app()
        self.search(at)
        self.approve(at)
        at.button(key='v21_sub_check_stock').click().run()
        manager = at.session_state['manager']
        self.assertTrue(self.applies(at))
        at.selectbox(key='v2_sub_candidate').set_value(product_key(manager.catalog[1])).run()
        self.assertFalse(self.applies(at))
        self.approve(at)
        self.assertFalse(self.applies(at))
        self.assertEqual(len(manager.inventory_calls), 1)
        at.button(key='v21_sub_check_stock').click().run()
        self.assertTrue(self.applies(at))
        self.assertEqual(manager.inventory_calls[-1][0].product_code, 'P-B')
        at.number_input(key='v2_sub_candidate_amount').set_value(300.0).run()
        self.assertFalse(self.applies(at))
        self.approve(at)
        self.assertFalse(self.applies(at))
        self.assertEqual(len(manager.inventory_calls), 2)

    def test_uncertain_or_insufficient_inventory_does_not_make_an_option(self):
        for case in ('partial', 'cached', 'zero', 'missing_time', 'unknown_source', 'outside_radius',
                     'missing_status','mixed_status','test_in_live'):
            with self.subTest(case=case):
                at = self.app()
                at.session_state['manager'].case = case
                self.search(at)
                self.approve(at)
                at.button(key='v21_sub_check_stock').click().run()
                self.assertEqual(len(at.exception), 0)
                self.assertFalse(self.applies(at))
                self.assertEqual(len(at.session_state['manager'].inventory_calls), 1)

    def test_failed_inventory_waits_for_another_explicit_click(self):
        at = self.app()
        manager = at.session_state['manager']
        manager.case = 'error'
        self.search(at)
        self.approve(at)
        at.button(key='v21_sub_check_stock').click().run()
        self.assertFalse(self.applies(at))
        self.assertTrue(any('확인하지 못했습니다' in message.value for message in at.warning))
        at.run()
        self.assertEqual(len(manager.inventory_calls), 1)
        at.button(key='v21_sub_check_stock').click().run()
        self.assertEqual(len(manager.inventory_calls), 2)

    def test_original_alias_conflict_is_not_filtered_out_of_evidence(self):
        at = self.app()
        at.session_state['manager'].case = 'original_conflict'
        self.search(at)
        self.approve(at)
        at.button(key='v21_sub_check_stock').click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.session_state['v21_sub_stock_rows']), 2)
        self.assertFalse(self.applies(at))

    def test_successful_option_changes_cart_only_after_apply_click(self):
        at = self.app()
        self.search(at)
        self.approve(at)
        at.button(key='v21_sub_check_stock').click().run()
        self.assertEqual(at.session_state['cart'][0].label, '원상품250ml')
        at.button(key='v2_sub_apply_0').click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state['cart'][0].label, '대체품A250ml')
        self.assertEqual(at.session_state['cart'][0].quantity, 2)
        self.assertIsNone(at.session_state['snapshot'])


if __name__ == '__main__':
    unittest.main()
