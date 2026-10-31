import csv
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import unittest

from core.cart_models import CartItem
from core.config import BASE_DIR
from core.data.csv_adapter import CsvAdapter
from core.models import StoreProduct
from core.services.basket import evaluate_stores
from core.services.catalog import load_catalog, resolve_cart_records
from core.services.substitutions import (
    SubstituteProduct, find_substitutes, load_substitute_catalog,
)


class SubstitutionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).isoformat()
        self.original = self.row('original', price=1800, promotion='1+1', stock=1)
        self.candidate = self.row('alternative', price=1600, promotion='1+1', stock=10)
        self.catalog = [self.metadata('original'), self.metadata('alternative')]

    def row(self, code, **changes):
        row = StoreProduct('CU', '확인된 시연 A점', '시연 주소 A', code,
                           barcode=code, product_code=code, price=1000, stock=10,
                           latitude=37.458115, longitude=126.952161,
                           distance_km=0.1, store_code='A', data_source='TEST',
                           updated_at=self.now, stock_updated_at=self.now,
                           price_updated_at=self.now, promotion_updated_at=self.now,
                           stock_source='명시적 TEST fixture', price_source='명시적 TEST fixture',
                           promotion_source='명시적 TEST fixture',
                           price_data_status='TEST', stock_data_status='TEST',
                           promotion_data_status='TEST')
        return replace(row, **changes)

    @staticmethod
    def metadata(code, **changes):
        entry = SubstituteProduct('CU', code, code, code, '명시적 시연 음료군', 250.0, 'ml')
        return replace(entry, **changes)

    def find(self, candidate=None, original=None, catalog=None, **kwargs):
        return find_substitutes(self.original if original is None else original,
                                [self.candidate if candidate is None else candidate], 2,
                                catalog=self.catalog if catalog is None else catalog, **kwargs)

    def test_short_original_stock_and_negative_additional_cost(self):
        option = self.find()[0]
        self.assertEqual((option.payable, option.extra_cost, option.volume_ratio), (1600, -200, 1.0))
        self.assertIn('-200원', option.reason)
        self.assertEqual(self.find(original=replace(self.original, stock_known=False))[0].payable, 1600)
        with self.assertRaises(FrozenInstanceError):
            option.payable = 1

    def test_group_unit_and_volume_must_match_explicitly(self):
        for changes in ({'group': '다른 군'}, {'unit': 'g'}, {'amount': 500}, {'amount': 0}):
            with self.subTest(changes=changes):
                self.assertEqual(self.find(catalog=[self.catalog[0], replace(self.catalog[1], **changes)]), [])
        boundary = [self.catalog[0], replace(self.catalog[1], amount=312.5)]
        self.assertEqual(self.find(catalog=boundary)[0].volume_ratio, 1.25)
        self.assertEqual(self.find(catalog=boundary, volume_tolerance=0.2), [])

    def test_missing_and_conflicting_metadata_never_infers_from_names(self):
        self.assertEqual(self.find(catalog=[]), [])
        self.assertEqual(self.find(catalog=[self.catalog[0]]), [])
        self.assertEqual(self.find(catalog=self.catalog + [replace(self.catalog[1], group='다른 군')]), [])
        no_id = replace(self.candidate, barcode='', product_code='', product_name='original')
        self.assertEqual(self.find(candidate=no_id), [])
        wrong_brand = replace(self.candidate, brand='GS25')
        self.assertEqual(self.find(candidate=wrong_brand), [])

    def test_extra_cost_ceiling_uses_whole_purchase_with_promotions(self):
        costly = replace(self.candidate, price=2000)
        self.assertEqual(self.find(candidate=costly), [])
        self.assertEqual(self.find(candidate=costly, max_extra=199), [])
        self.assertEqual(self.find(candidate=costly, max_extra=200)[0].extra_cost, 200)
        # Two non-promotional units cost 3,200, not the displayed unit price.
        regular = replace(self.candidate, promotion='')
        self.assertEqual(self.find(candidate=regular, max_extra=1399), [])
        self.assertEqual(self.find(candidate=regular, max_extra=1400)[0].payable, 3200)

    def test_candidate_exact_stock_is_required(self):
        for changes in ({'stock': 0}, {'stock': 1}, {'stock_known': False}, {'stock_quantity_exact': False}):
            with self.subTest(changes=changes):
                self.assertEqual(self.find(candidate=replace(self.candidate, **changes)), [])

    def test_original_price_and_promotion_are_required(self):
        for changes in ({'price': 0}, {'promotion_known': False}, {'promotion': '3+1'},
                        {'price_source': ''}, {'promotion_source': ''}, {'price_updated_at': ''},
                        {'promotion_updated_at': ''}, {'price_data_status': 'UNKNOWN'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.find(original=replace(self.original, **changes)), [])

    def test_cache_partial_error_and_unknown_evidence_are_rejected(self):
        for status in ('CACHE', 'PARTIAL', 'ERROR', 'UNKNOWN'):
            with self.subTest(status=status):
                self.assertEqual(self.find(candidate=replace(self.candidate, data_source=status)), [])
                self.assertEqual(self.find(original=replace(self.original, data_source=status)), [])
                for field in ('price', 'promotion', 'stock'):
                    self.assertEqual(self.find(candidate=replace(self.candidate,
                                                                **{f'{field}_data_status': status})), [])

    def test_candidate_needs_each_source_and_timestamp(self):
        for field in ('price', 'promotion', 'stock'):
            self.assertEqual(self.find(candidate=replace(self.candidate, **{f'{field}_source': ''})), [])
            self.assertEqual(self.find(candidate=replace(self.candidate, **{f'{field}_updated_at': ''})), [])

    def test_live_stale_price_promotion_and_stock_cannot_be_recommended(self):
        live = dict(data_source='LIVE', price_data_status='LIVE', promotion_data_status='LIVE',
                    stock_data_status='LIVE')
        original, candidate = replace(self.original, **live), replace(self.candidate, **live)
        self.assertEqual(len(self.find(original=original, candidate=candidate)), 1)
        old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()
        for stamp in (old, future, 'invalid', '2026-10-31T10:00:00'):
            for field in ('price', 'promotion', 'stock'):
                with self.subTest(stamp=stamp, field=field):
                    self.assertEqual(self.find(original=original, candidate=replace(
                        candidate, **{f'{field}_updated_at': stamp})), [])
                    if field != 'stock':
                        self.assertEqual(self.find(candidate=candidate, original=replace(
                            original, **{f'{field}_updated_at': stamp})), [])

    def test_same_original_product_is_never_an_alternative(self):
        same = replace(self.candidate, barcode='original', product_code='original')
        self.assertEqual(self.find(candidate=same), [])
        other_brand = replace(same, brand='GS25')
        metadata = self.catalog + [replace(self.catalog[0], brand='GS25')]
        self.assertEqual(self.find(candidate=other_brand, catalog=metadata), [])

    def test_verified_location_and_store_identity_are_required(self):
        for changes in ({'latitude': None}, {'longitude': None}, {'latitude': 91.0},
                        {'longitude': float('nan')}, {'distance_km': -1.0},
                        {'store_code': '', 'address': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(self.find(candidate=replace(self.candidate, **changes)), [])

    def test_conflicting_latest_duplicates_are_rejected(self):
        conflict = replace(self.candidate, stock=0)
        self.assertEqual(find_substitutes(self.original, [self.candidate, conflict], 2,
                                         catalog=self.catalog), [])
        original_conflict = replace(self.original, price=1900)
        self.assertEqual(find_substitutes(self.original, [self.candidate, original_conflict], 2,
                                         catalog=self.catalog), [])
        older = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        prior = replace(conflict, stock_updated_at=older)
        self.assertEqual(len(find_substitutes(self.original, [prior, self.candidate], 2,
                                             catalog=self.catalog)), 1)

    def test_identical_repeats_deduplicate_and_order_is_deterministic(self):
        near = replace(self.candidate, store_code='B', address='시연 주소 B', distance_km=0.05)
        cheaper = replace(self.candidate, store_code='C', address='시연 주소 C', price=1500, distance_km=0.3)
        options = find_substitutes(self.original, [self.candidate, near, cheaper, self.candidate], 2,
                                   catalog=self.catalog)
        self.assertEqual([option.record.store_code for option in options], ['C', 'B', 'A'])
        reversed_options = find_substitutes(self.original, [self.candidate, cheaper, near], 2,
                                            catalog=self.catalog)
        self.assertEqual(options, reversed_options)
        unknown_distance = replace(self.candidate, distance_km=None)
        for repeats in ([unknown_distance, self.candidate], [self.candidate, unknown_distance]):
            deduplicated = find_substitutes(self.original, repeats, 2, catalog=self.catalog)
            self.assertEqual(len(deduplicated), 1)
            self.assertEqual(deduplicated[0].record.distance_km, 0.1)

    def test_invalid_controls_are_rejected(self):
        for kwargs in ({'max_extra': -1}, {'max_extra': True}, {'volume_tolerance': -0.1},
                       {'volume_tolerance': float('nan')}):
            with self.assertRaises(ValueError):
                self.find(**kwargs)


class SubstitutionFixtureTests(unittest.TestCase):
    def test_v2_fixture_preserves_all_original_observations_and_totals(self):
        original_path = BASE_DIR / 'sample_data/demo_products.csv'
        v2_path = BASE_DIR / 'sample_data/v2_demo_products.csv'
        self.assertTrue(v2_path.read_bytes().startswith(original_path.read_bytes()))
        with original_path.open(encoding='utf-8-sig', newline='') as stream:
            original_csv = list(csv.DictReader(stream))
        with v2_path.open(encoding='utf-8-sig', newline='') as stream:
            v2_csv = list(csv.DictReader(stream))
        self.assertEqual(len(original_csv), 18)
        self.assertEqual(len({row['barcode'] for row in original_csv}), 3)
        self.assertEqual(v2_csv[:18], original_csv)
        self.assertEqual(len(v2_csv), 30)
        cart = [CartItem('drink', '음료', 2), CartItem('noodle', '컵라면', 1), CartItem('snack', '과자', 1)]
        links = load_catalog()
        def totals(path):
            rows = CsvAdapter(path).search('', 37.458115, 126.952161)
            results = evaluate_stores(cart, resolve_cart_records(cart, rows, links),
                                      37.458115, 126.952161, 2)
            return {row.store_key: (row.total, row.reference_total, row.eligible) for row in results}
        self.assertEqual(totals(original_path), totals(v2_path))
        self.assertEqual(totals(v2_path)['CU|A'][0], 5300)
        self.assertEqual(totals(v2_path)['GS25|B'][0], 6500)

    def test_bundled_metadata_and_alternative_stock_are_explicit_test_data(self):
        catalog = load_substitute_catalog()
        self.assertEqual(len(catalog), 20)
        self.assertTrue(all(isinstance(row.amount, float) for row in catalog))
        rows = CsvAdapter(BASE_DIR / 'sample_data/v2_demo_products.csv').search('', 37.458115, 126.952161)
        alternatives = [row for row in rows if row.barcode.startswith('DEMO-ALT-')]
        self.assertEqual(len(alternatives), 12)
        self.assertEqual({row.store_code for row in alternatives}, set('ABCDEF'))
        self.assertTrue(all(row.data_source == 'TEST' and row.promotion_known and row.stock >= 2
                            and row.stock_quantity_exact for row in alternatives))
        short_original = next(row for row in rows if row.store_code == 'C' and row.barcode == 'DEMO-DRINK')
        options = find_substitutes(short_original, rows, 2, catalog=catalog)
        self.assertTrue(options)
        self.assertTrue(all(row.record.barcode == 'DEMO-ALT-DRINK' for row in options))


if __name__ == '__main__':
    unittest.main()
