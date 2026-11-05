"""Untrusted names must remain text in every supported CSV download path."""
import csv
import io
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from core.cart_models import CartItem, LineEvaluation, QuerySnapshot, StoreEvaluation
from core.models import DataLoadResult, StoreProduct
from core.services.exporting import comparison_csv
from core.ui.v2_panels import _split
from core.security.csv_cells import TEXT_PREFIX, csv_cell, csv_safe_frame
from core.cart_models import CatalogLink
from core.services.catalog import resolve_cart_records
from core.services.basket import evaluate_stores
from core.services.v2_common import apply_current_evidence
from tests.live_fixtures import verified_offer


def decoded_rows(data):
    return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))


class CsvExportSecurityTests(unittest.TestCase):
    def store(self):
        row = StoreProduct('CU', '=1+1', '서울', '\n=1+1', store_code='S', stock=1)
        line = LineEvaluation('i', 1, None, None, 'ENOUGH', 'UNKNOWN', '미확인', row)
        return StoreEvaluation('CU|S', 'CU', '=1+1', .1, [line], None, 0, None, False, 'LIVE', [])

    def test_comparison_csv_neutralizes_line_feed_prefixed_provider_name(self):
        store = self.store()
        snapshot = QuerySnapshot([store.lines[0].record], [], {}, (), '시작', '완료')
        output = comparison_csv([store], snapshot)
        rows = list(csv.DictReader(io.StringIO(output.decode('utf-8-sig'))))
        self.assertTrue(rows[0]['상품명'].startswith('[텍스트] '))
        self.assertEqual(store.lines[0].record.product_name, '\n=1+1')

    def test_split_download_neutralizes_store_and_cart_names(self):
        store = self.store()
        cart = [CartItem('i', '  =1+1', 1)]
        plan = SimpleNamespace(assignments={'i':'CU|S'}, store_count=1, total=1200,
                               store_names=(store.store_name,), distance_km=.1, within_budget=True)
        with patch('core.ui.v2_panels.st') as ui, \
             patch('core.ui.v2_panels.compare_purchase_plans', return_value=[plan]):
            _split([store], cart, 2000, 37.5, 127)
        download = ui.download_button.call_args
        self.assertEqual(download.args[0], '구매 목록 CSV 저장')
        rows = decoded_rows(download.args[1])
        self.assertTrue(rows[1][0].startswith('[텍스트] '))
        self.assertTrue(rows[1][1].startswith('[텍스트] '))
        self.assertEqual(rows[1][2], '1')
        self.assertEqual(rows[2][1], '1200')

    def test_formula_markers_and_ignored_leading_characters_are_labeled(self):
        samples = ['=1+1', '+SUM(1,1)', '-SUM(1,1)', '@SUM(1,1)', '\n=1+1',
                   '\r@SUM(1,1)', '\t안내', '  =1+1', '\x00 \t=1+1',
                   '\u200b\ufeff\u00a0=1+1', '\u200b＠SUM(1,1)', '  ＝1+1',
                   '＋SUM(1,1)', '－SUM(1,1)', "  '\t=1+1"]
        for value in samples:
            with self.subTest(value=repr(value)):
                result = csv_cell(value)
                self.assertEqual(result, TEXT_PREFIX + value)
                self.assertEqual(csv_cell(result), result)

    def test_regular_korean_names_identifiers_and_numeric_values_are_preserved(self):
        samples = ['롯데)펩시콜라600ml', '1+1', '2+1', '8801056193010', '  일반 안내',
                   '3개 · -200원', '안내\n=중간문자는 수식이 아님', '', -4, -2.5, 0, 2500, True]
        for value in samples:
            with self.subTest(value=value):
                self.assertEqual(csv_cell(value), value)
                self.assertIs(type(csv_cell(value)), type(value))
        self.assertEqual(csv_cell(None), '')

    def test_csv_quoting_keeps_delimiters_and_newlines_inside_the_same_text_cell(self):
        store = self.store()
        attack = '=1+1";,@SUM(1,1)\n=2+2'
        store.lines[0].record.product_name = attack
        snapshot = QuerySnapshot([store.lines[0].record], [], {}, (), '시작', '완료')
        rows = decoded_rows(comparison_csv([store], snapshot))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), len(rows[1]))
        product_column = rows[0].index('상품명')
        self.assertEqual(rows[1][product_column], TEXT_PREFIX + attack)

    def test_dataframe_copy_protects_builtin_csv_without_changing_calculation_values(self):
        original = pd.DataFrame({'상품명':['=1+1','일반 상품',' \t＠SUM(1,1)'],
                                 '수량':[1,2,3],'가격':[-2.5,0,2500], '누락':[None,None,None]})
        before = original.copy(deep=True)
        safe = csv_safe_frame(original)
        pd.testing.assert_frame_equal(original, before)
        self.assertEqual(safe['상품명'].tolist(), [TEXT_PREFIX+'=1+1','일반 상품',TEXT_PREFIX+' \t＠SUM(1,1)'])
        self.assertEqual(safe['수량'].tolist(), [1,2,3])
        self.assertEqual(safe['가격'].tolist(), [-2.5,0,2500])
        self.assertTrue(safe['누락'].isna().all())
        self.assertEqual(safe['수량'].dtype, original['수량'].dtype)
        parsed = list(csv.DictReader(io.StringIO(safe.to_csv(index=False))))
        self.assertEqual(parsed[0]['상품명'], TEXT_PREFIX+'=1+1')

    def test_dataframe_headers_and_index_are_also_literal_and_original_is_untouched(self):
        original = pd.DataFrame({'=열':['=상품']}, index=['=행'])
        safe = csv_safe_frame(original)
        self.assertEqual(list(safe.columns), [TEXT_PREFIX+'=열'])
        self.assertEqual(list(safe.index), [TEXT_PREFIX+'=행'])
        self.assertEqual(list(original.columns), ['=열'])
        self.assertEqual(list(original.index), ['=행'])

    def test_split_download_quotes_a_malicious_name_as_one_cell(self):
        store = self.store()
        cart = [CartItem('i', '=1+1";,@SUM(1,1)\n=2+2', 1)]
        plan = SimpleNamespace(assignments={'i':'CU|S'}, store_count=1, total=1200,
                               store_names=(store.store_name,), distance_km=.1, within_budget=True)
        with patch('core.ui.v2_panels.st') as ui, \
             patch('core.ui.v2_panels.compare_purchase_plans', return_value=[plan]):
            _split([store], cart, 2000, 37.5, 127)
        rows = decoded_rows(ui.download_button.call_args.args[1])
        self.assertTrue(all(len(row)==3 for row in rows))
        self.assertEqual(rows[1][1], TEXT_PREFIX+cart[0].label)
        self.assertEqual(cart[0].quantity, 1)

    def test_provider_model_through_real_cart_evaluation_and_plan_reaches_safe_download(self):
        stamp = datetime.now(timezone.utc).isoformat()
        row = StoreProduct('CU', '=1+1', '서울', '=1+1', product_code='P', store_code='S',
                           price=1200, promotion='1+1', promotion_known=True, stock=2,
                           stock_known=True, stock_quantity_exact=True, latitude=37.5, longitude=127,
                           data_source='LIVE', price_data_status='LIVE', stock_data_status='LIVE',
                           promotion_data_status='LIVE', price_source='모의 공개 원본',
                           stock_source='모의 공개 원본', promotion_source='모의 공개 원본',
                           received_at=stamp, price_updated_at=stamp, stock_source_updated_at=stamp,
                           promotion_updated_at=stamp, **verified_offer())
        cart = [CartItem('i', row.product_name, 1)]
        links = [CatalogLink('i', 'CU', '', 'P', row.product_name)]
        resolved = resolve_cart_records(cart, [row], links)
        stores = apply_current_evidence(evaluate_stores(cart, resolved, 37.5, 127, 2))
        self.assertTrue(stores[0].eligible)
        with patch('core.ui.v2_panels.st') as ui:
            _split(stores, cart, 2000, 37.5, 127)
        rows = decoded_rows(ui.download_button.call_args.args[1])
        self.assertEqual(rows[1], [TEXT_PREFIX+'=1+1', TEXT_PREFIX+'=1+1', '1'])
        self.assertEqual(row.product_name, '=1+1')
        self.assertEqual(stores[0].total, 1200)

    def test_received_grids_pass_safe_copies_to_the_builtin_download_toolbar(self):
        from core.ui.evidence import render_received_observations
        row = self.store().lines[0].record
        status = DataLoadResult('CU', [row], 'PARTIAL', '=1+1')
        snapshot = QuerySnapshot([row], [status], {}, (), '시작', '완료')
        with patch('core.ui.evidence.st') as ui:
            render_received_observations(snapshot, 37.5, 127, 2)
        self.assertEqual(ui.dataframe.call_count, 2)
        observations = ui.dataframe.call_args_list[0].args[0]
        coverage = ui.dataframe.call_args_list[1].args[0]
        self.assertEqual(observations.iloc[0]['상품'], TEXT_PREFIX+'\n=1+1')
        self.assertEqual(observations.iloc[0]['매장'], TEXT_PREFIX+'=1+1')
        self.assertEqual(coverage.iloc[0]['안내'], TEXT_PREFIX+'=1+1')
        self.assertEqual(row.product_name, '\n=1+1')
        self.assertEqual(status.message, '=1+1')


if __name__ == '__main__':
    unittest.main()
