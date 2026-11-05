"""식별자 혼동과 규격 차이로 인한 잘못된 자동 연결을 방지한다."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from core.models import StoreProduct
from core.cart_models import CatalogLink
from core.services.product_matching import (
    normalized_gtin, product_signature, same_product, shared_gtin,
)
from core.services.catalog import (
    catalog_link_for, load_catalog, load_user_catalog, matches_link,
    reusable_links_for, save_confirmed_links,
)
from core.services.product_list import list_products


def row(name='제조사)딸기우유 250ml', **kwargs):
    return StoreProduct('CU', 'A점', '주소', name, **kwargs)


class ProductIdentityTests(unittest.TestCase):
    def test_common_gtin_normalizes_padding_but_not_internal_codes(self):
        a = row(barcode='8801007021652')
        b = replace(a, barcode='08801007021652', brand='GS25')
        self.assertTrue(normalized_gtin(a.barcode))
        self.assertTrue(shared_gtin(a, b))
        self.assertFalse(same_product(a, b))  # 브랜드 행사/가격은 섞이지 않는다.
        self.assertFalse(shared_gtin(replace(a, barcode='', product_code=a.barcode), b))
        self.assertFalse(normalized_gtin('8801007021653'))  # 잘못된 체크 숫자

    def test_namespace_collision_is_not_identity(self):
        a = row('서로 다른 상품', barcode='12345678')
        b = row('이름이 다른 상품', product_code='12345678')
        self.assertFalse(same_product(a, b))

    def test_authoritative_barcode_conflict_beats_same_internal_code(self):
        a = row(barcode='8801007021652', product_code='P1')
        self.assertFalse(same_product(a, replace(a, barcode='8801007021653')))

    def test_same_explicit_barcode_can_join_name_alias_without_variant_conflict(self):
        a = row('우유', barcode='8801007021652')
        self.assertTrue(same_product(a, replace(a, product_name='제조사)딸기우유 250ml')))

    def test_known_volume_flavor_and_package_conflicts_beat_identifier(self):
        original = row(product_code='P1')
        for name in ('제조사)딸기우유 500ml', '제조사)초코우유 250ml'):
            with self.subTest(name=name):
                self.assertFalse(same_product(original, replace(original, product_name=name)))
        package = row('제조사)딸기우유 250ml x2', product_code='P1')
        self.assertFalse(same_product(package, replace(package, product_name='제조사)딸기우유 250ml x3')))

    def test_metadata_conflicts_are_checked_when_name_omits_details(self):
        original = row('우유', barcode='8801007021652', volume='250ml', flavor='딸기', pack_count='2')
        for changes in ({'volume': '500ml'}, {'flavor': '초코'}, {'pack_count': '3'}, {'manufacturer': '다른회사'}):
            a = replace(original, manufacturer='회사')
            with self.subTest(changes=changes):
                self.assertFalse(same_product(a, replace(a, **changes)))

    def test_full_name_manufacturer_and_equivalent_units_allow_name_fallback(self):
        a = row('회사)우유 500ml x2')
        b = row('회사) 우 유 0.5L × 2')
        self.assertTrue(same_product(a, b))
        self.assertEqual(product_signature(a).pack_count, 2)
        self.assertEqual(product_signature(row('회사)우유500mlx2')).volume, ('500', 'ml'))

    def test_generic_missing_manufacturer_or_volume_is_uncertain(self):
        for name in ('콜라', '콜라500ml', '회사)콜라'):
            with self.subTest(name=name):
                self.assertFalse(same_product(row(name), row(name)))

    def test_other_flavor_or_package_is_not_partial_name_match(self):
        a = row('회사)음료 250ml x2')
        for name in ('다른회사)음료250ml x2', '회사)음료250ml', '회사)음료250ml x3', '회사)음료자몽250ml x2'):
            with self.subTest(name=name):
                self.assertFalse(same_product(a, row(name)))

    def test_grams_and_millilitres_are_different_dimensions(self):
        self.assertFalse(same_product(row('회사)푸딩250g'), row('회사)푸딩250ml')))

    def test_source_metadata_disagreement_with_its_own_name_is_uncertain(self):
        a = row('회사)딸기우유250ml x2', barcode='8801007021652')
        for changes in ({'manufacturer': '다른회사'}, {'volume': '500ml'},
                        {'flavor': '초코'}, {'pack_count': '3'}):
            with self.subTest(changes=changes):
                self.assertFalse(same_product(a, replace(a, **changes)))

    def test_list_does_not_collapse_conflicting_structured_variants(self):
        original = row('우유', product_code='P1', volume='250ml')
        self.assertEqual(len(list_products([original, replace(original, volume='500ml')])), 2)


class ConfirmedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'user_data' / 'confirmed_links.csv'
        self.a = row('회사)딸기우유250ml', barcode='8801007021652', product_code='A1', manufacturer='회사', volume='250ml')
        self.b = replace(self.a, brand='GS25', product_code='B1')
        self.links = [catalog_link_for(self.a, 'saved'), catalog_link_for(self.b, 'saved')]

    def test_saved_links_require_explicit_confirmation(self):
        with self.assertRaises(ValueError):
            save_confirmed_links(self.links, confirmed=False, path=self.path)
        self.assertFalse(self.path.exists())

    def test_round_trip_preserves_identifiers_and_metadata_and_reuses_group(self):
        save_confirmed_links(self.links, confirmed=True, path=self.path)
        saved = load_user_catalog(self.path)
        self.assertEqual(saved, self.links)
        self.assertEqual(reusable_links_for(self.a, saved), self.links)
        self.assertEqual(saved[0].manufacturer, '회사')
        self.assertEqual(saved[0].volume, '250ml')

    def test_legacy_demo_schema_still_loads_without_new_optional_columns(self):
        links = load_catalog()
        self.assertEqual(len(links), 12)
        self.assertEqual(links[0].volume, '')

    def test_explicit_link_rejects_variant_conflict(self):
        link = catalog_link_for(self.a, 'saved')
        self.assertFalse(matches_link(replace(self.a, volume='500ml'), link))
        self.assertFalse(matches_link(replace(self.a, barcode='different'), link))

    def test_link_does_not_cross_barcode_and_internal_code(self):
        link = CatalogLink('saved', 'CU', 'same-text', '', '상품')
        other = row('상품', product_code='same-text')
        self.assertFalse(matches_link(other, link))

    def test_conflicting_saved_groups_do_not_guess(self):
        second_group = [replace(self.links[0], item_id='other'),
                        replace(self.links[1], item_id='other', product_code='DIFFERENT', barcode='DIFFERENT')]
        save_confirmed_links(self.links, confirmed=True, path=self.path)
        save_confirmed_links(second_group, confirmed=True, path=self.path)
        self.assertEqual(reusable_links_for(self.a, load_user_catalog(self.path)), [])

    def test_equivalent_saved_groups_can_reuse_without_ambiguity(self):
        second_group = [replace(link, item_id='other') for link in self.links]
        saved = self.links + second_group
        self.assertEqual(len(reusable_links_for(self.a, saved)), 2)

    def test_missing_codes_and_duplicate_brand_are_not_persisted(self):
        no_id = CatalogLink('saved', 'CU', '', '', '우유')
        with self.assertRaises(ValueError):
            save_confirmed_links([no_id], confirmed=True, path=self.path)
        with self.assertRaises(ValueError):
            save_confirmed_links([self.links[0], self.links[0]], confirmed=True, path=self.path)
        self.assertFalse(self.path.exists())

    def test_replacing_group_removes_old_links_and_keeps_other_group(self):
        save_confirmed_links(self.links, confirmed=True, path=self.path)
        other = [replace(self.links[0], item_id='other')]
        save_confirmed_links(other, confirmed=True, path=self.path)
        replacement = [replace(self.links[0], product_code='NEW', barcode='NEW')]
        save_confirmed_links(replacement, confirmed=True, path=self.path)
        self.assertEqual(set(load_user_catalog(self.path)), set(other + replacement))

    def test_concurrent_saves_keep_both_groups(self):
        other = [replace(link, item_id='other') for link in self.links]
        with ThreadPoolExecutor(2) as executor:
            results = [executor.submit(save_confirmed_links, group, confirmed=True, path=self.path)
                       for group in (self.links, other)]
            for future in results:
                future.result()
        self.assertEqual(len(load_user_catalog(self.path)), 4)
        self.assertEqual(list(self.path.parent.glob('.links-*.tmp')), [])

    def test_broken_csv_is_reported_and_never_overwritten(self):
        self.path.parent.mkdir()
        original = '이것은 연결표가 아닙니다.\n'
        self.path.write_text(original, encoding='utf-8')
        with self.assertRaises(ValueError):
            load_user_catalog(self.path)
        with self.assertRaises(ValueError):
            save_confirmed_links(self.links, confirmed=True, path=self.path)
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
