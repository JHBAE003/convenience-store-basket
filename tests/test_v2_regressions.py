"""Regression checks for the concrete v2 failures found in independent review."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from core.cart_models import CartItem, CatalogLink
from core.models import DataLoadResult, StoreProduct
from core.services.catalog import load_catalog
from core.services.product_list import product_key
from core.services.substitutions import SubstituteProduct, find_substitutes
from core.ui import v2_actions


APP = Path(__file__).resolve().parents[1] / 'streamlit_app.py'


def live_row(name, barcode, **changes):
    observed = datetime.now().astimezone().isoformat()
    row = StoreProduct(
        'CU', '검증 매장', '서울 관악구', name, barcode=barcode,
        price=1200, promotion='1+1', stock=5, stock_quantity_exact=True,
        latitude=37.458615, longitude=126.952661, store_code='A', data_source='LIVE',
        updated_at=observed, price_updated_at=observed, stock_updated_at=observed,
        promotion_updated_at=observed, price_source='독립 모의 제공자',
        stock_source='독립 모의 제공자', promotion_source='독립 모의 제공자',
        price_data_status='LIVE', stock_data_status='LIVE', promotion_data_status='LIVE',
    )
    return replace(row, **changes)


def approved_metadata(original, candidate):
    return [SubstituteProduct(row.brand, row.barcode, row.product_code,
                              row.product_name, '명시적으로 승인한 음료', 250.0, 'ml')
            for row in (original, candidate)]


class V2RegressionAppTests(unittest.TestCase):
    def setUp(self):
        remote = patch('core.ui.product_images.remote_photo', return_value=None)
        remote.start()
        self.addCleanup(remote.stop)

    def app(self):
        at = AppTest.from_file(str(APP), default_timeout=30).run()
        self.addCleanup(at.session_state['temp_directory'].cleanup)
        self.assertEqual(len(at.exception), 0)
        return at

    def demo_substitution(self):
        at = self.app()
        at.radio(key='mode').set_value('TEST').run()
        at.selectbox(key='v2_demo_scenario').set_value('품절 대체').run()
        at.button(key='v2_load_scenario').click().run()
        at.button(key='v2_sub_search').click().run()
        at.button(key='v2_sub_apply_0').click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertIn('대체 음료', at.session_state['cart'][0].label)
        self.assertEqual({link.barcode for link in at.session_state['links']
                          if link.item_id == 'drink'}, {'DEMO-ALT-DRINK'})
        return at

    def live_cart(self, original, alternatives=()):
        def search(keyword, *args, **kwargs):
            rows = list(alternatives) if keyword == '대체후보' else [original]
            return rows, [DataLoadResult('CU', rows, 'LIVE', '독립 모의 조회 성공')]

        provider = patch('core.data.data_manager.DataManager.search_all', side_effect=search)
        provider.start()
        self.addCleanup(provider.stop)
        at = self.app()
        at.text_input(key='live_keyword').set_value('원상품').run()
        at.button(key='search_live').click().run()
        next(button for button in at.button
             if button.key and button.key.startswith('card_')).click().run()
        at.button(key='compare').click().run()
        self.assertEqual(len(at.exception), 0)
        return at

    def open_live_alternatives(self, original, alternatives, selected):
        at = self.live_cart(original, alternatives)
        at.pills(key='v2_tool').set_value('품절 대체').run()
        at.text_input(key='v2_sub_keyword').set_value('대체후보').run()
        at.button(key='v2_sub_search').click().run()
        at.selectbox(key='v2_sub_candidate').set_value(product_key(selected)).run()
        self.assertEqual(len(at.exception), 0)
        return at

    @staticmethod
    def approval(at):
        return next(checkbox for checkbox in at.checkbox
                    if checkbox.key and checkbox.key.startswith('v2_sub_approve_'))

    @staticmethod
    def has_substitute_apply(at):
        return any(button.key and button.key.startswith('v2_sub_apply_') for button in at.button)

    def test_demo_reload_restores_original_codes_after_substitution(self):
        expected = {link for link in load_catalog() if link.item_id == 'drink'}
        for reload_kind in ('scenario', 'example_cart'):
            with self.subTest(reload=reload_kind):
                at = self.demo_substitution()
                if reload_kind == 'scenario':
                    at.selectbox(key='v2_demo_scenario').set_value('예산 맞추기').run()
                    at.button(key='v2_load_scenario').click().run()
                else:
                    at.button(key='load_demo').click().run()
                    self.assertIsNone(at.session_state['snapshot'])
                    at.button(key='compare').click().run()
                self.assertEqual(len(at.exception), 0)
                drink = next(item for item in at.session_state['cart'] if item.item_id == 'drink')
                self.assertEqual((drink.label, drink.quantity), ('시연 음료 250ml', 2))
                self.assertEqual({link for link in at.session_state['links']
                                  if link.item_id == 'drink'}, expected)
                self.assertEqual(at.session_state['recommendations'].cheapest.total, 5300)

    def test_live_approval_resets_when_candidate_or_volume_changes(self):
        original = live_row('원상품250ml', '8801111111111')
        candidate_a = live_row('대체후보A250ml', '8802222222222')
        candidate_b = live_row('대체후보B250ml', '8803333333333')
        at = self.open_live_alternatives(original, [candidate_a, candidate_b], candidate_a)
        self.assertFalse(self.approval(at).value)
        self.approval(at).set_value(True).run()
        self.assertTrue(self.has_substitute_apply(at))

        changes = [('candidate', lambda: at.selectbox(key='v2_sub_candidate').set_value(product_key(candidate_b))),
                   ('candidate_volume', lambda: at.number_input(key='v2_sub_candidate_amount').set_value(300.0)),
                   ('original_volume', lambda: at.number_input(key='v2_sub_original_amount').set_value(300.0)),
                   ('unit', lambda: at.selectbox(key='v2_sub_unit').set_value('g'))]
        for field, change in changes:
            with self.subTest(changed=field):
                previous_key = self.approval(at).key
                change().run()
                self.assertEqual(len(at.exception), 0)
                self.assertNotEqual(self.approval(at).key, previous_key)
                self.assertFalse(self.approval(at).value)
                self.assertFalse(at.session_state['v2_sub_approved'])
                self.assertFalse(self.has_substitute_apply(at))
                self.approval(at).set_value(True).run()
                self.assertTrue(self.has_substitute_apply(at))
        at.button(key='v2_sub_apply_0').click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state['cart'][0].label, candidate_b.product_name)

    def test_live_search_preserves_original_and_candidate_alias_conflicts(self):
        original = live_row('원상품250ml', '8801111111111')
        candidate = live_row('대체후보250ml', '8802222222222')
        cases = [('original_alias_conflict',
                  [candidate, replace(original, product_name='원상품 별칭250ml', price=1000)], False),
                 ('candidate_alias_conflict',
                  [candidate, replace(candidate, product_name='대체후보 별칭250ml', price=1000)], False),
                 ('identical_candidate_alias',
                  [candidate, replace(candidate, product_name='대체후보 별칭250ml')], True)]
        for name, rows, allowed in cases:
            with self.subTest(evidence=name):
                at = self.open_live_alternatives(original, rows, candidate)
                self.approval(at).set_value(True).run()
                self.assertEqual(len(at.exception), 0)
                self.assertEqual(len(at.session_state['v2_alt_rows']), len(rows))
                self.assertEqual(self.has_substitute_apply(at), allowed)
                self.assertEqual(at.session_state['cart'][0].label, original.product_name)
                if allowed:
                    at.button(key='v2_sub_apply_0').click().run()
                    # Identical aliases can supply either display name; the
                    # approved product identity and requested quantity matter.
                    self.assertIn(at.session_state['cart'][0].label,
                                  {row.product_name for row in rows if row.barcode == candidate.barcode})
                    self.assertEqual(at.session_state['cart'][0].quantity, 1)
                    self.assertEqual({link.barcode for link in at.session_state['links']}, {candidate.barcode})
                    self.assertIsNone(at.session_state['snapshot'])
                else:
                    self.assertTrue(any('조건을 모두 만족' in message.value for message in at.info))

    def test_stale_price_or_promotion_never_appears_as_headline_recommendation(self):
        for field in ('price_updated_at', 'promotion_updated_at'):
            with self.subTest(stale=field):
                old = (datetime.now().astimezone() - timedelta(minutes=10)).isoformat()
                original = live_row('원상품250ml', '8801111111111', **{field: old})
                at = self.live_cart(original)
                self.assertEqual(len(at.session_state['snapshot'].records), 1)
                self.assertIsNone(at.session_state['recommendations'].cheapest)
                self.assertIsNone(at.session_state['recommendations'].nearest)
                self.assertFalse(any(metric.label == '최저가 매장' for metric in at.metric))
                self.assertTrue(any('확정 추천이 없습니다' in warning.value for warning in at.warning))

        # The same source with fresh timestamps must remain recommendable.
        at = self.live_cart(live_row('원상품250ml', '8801111111111'))
        self.assertEqual(at.session_state['recommendations'].cheapest.total, 1200)
        self.assertTrue(any(metric.label == '최저가 매장' for metric in at.metric))

    def test_blank_budget_does_not_crash_or_render_stale_recommendations(self):
        at = self.app()
        at.radio(key='mode').set_value('TEST').run()
        at.button(key='load_demo').click().run()
        at.button(key='compare').click().run()
        before = list(at.session_state['cart'])
        self.assertIsNotNone(at.session_state['recommendations'].cheapest)
        at.number_input(key='budget').set_value(None).run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state['cart'], before)
        self.assertTrue(any('예산을 입력' in message.value for message in at.info))
        self.assertFalse(any(metric.label == '최저가 매장' for metric in at.metric))
        at.number_input(key='budget').set_value(6000).run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state['recommendations'].cheapest.total, 5300)


class AttributeState(dict):
    """Minimal state adapter for testing callbacks without a running page."""
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class V2RegressionCallbackTests(unittest.TestCase):
    def test_substitution_click_rechecks_all_original_and_alias_evidence(self):
        original = live_row('원상품250ml', '8801111111111')
        candidate = live_row('대체후보250ml', '8802222222222')
        metadata = approved_metadata(original, candidate)
        option = find_substitutes(original, [candidate], 1, catalog=metadata)[0]
        for conflict in (replace(original, price=1000, product_name='원상품 별칭250ml'),
                         replace(candidate, price=1000, product_name='대체후보 별칭250ml')):
            with self.subTest(conflict=conflict.product_name):
                state = AttributeState(
                    cart=[CartItem('drink', original.product_name, 1)], mode='LIVE',
                    latitude=37.458115, longitude=126.952161, brands=['CU'], radius=2.0,
                    budget=6000, links=[CatalogLink('drink', 'CU', original.barcode, '', original.product_name)],
                    snapshot=SimpleNamespace(completed_at=original.updated_at),
                    v2_sub_approved=True, v2_approval_context=('approved pair',),
                )
                ui = SimpleNamespace(session_state=state, error=Mock(), toast=Mock())
                before_cart, before_links = list(state.cart), list(state.links)
                with patch.object(v2_actions, 'st', ui):
                    context = v2_actions.comparison_context()
                    v2_actions.apply_substitute('drink', option, original, metadata, 0, 0.25,
                                                context, state.v2_approval_context,
                                                [candidate, conflict])
                self.assertEqual(state.cart, before_cart)
                self.assertEqual(state.links, before_links)
                ui.error.assert_called_once()
                ui.toast.assert_not_called()


if __name__ == '__main__':
    unittest.main()
