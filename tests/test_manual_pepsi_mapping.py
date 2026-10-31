"""실제 브랜드별 펩시 표기는 수동 확인에서만 다루고 규격 충돌을 보존한다."""
from dataclasses import asdict, replace
import unittest

from core.models import StoreProduct
from core.services.product_matching import (
    product_signature, same_product, conflicting_variants, conflicting_mapping_variants,
)


class ManualPepsiMappingTests(unittest.TestCase):
    def row(self,name,**kwargs):
        return StoreProduct('CU','','',name,product_code='8801056193010',**kwargs)

    def test_known_ascii_package_suffix_does_not_hide_volume(self):
        for suffix in ('PET','pet','PACK','CAN','BOTTLE','PETx2'):
            with self.subTest(suffix=suffix):
                self.assertEqual(product_signature(self.row('롯데)펩시콜라600ML'+suffix)).volume,('600','ml'))
        self.assertIsNone(product_signature(self.row('회사)액체600MLOIL')).volume)
        self.assertIsNone(product_signature(self.row('회사)액체600MLPETRI')).volume)
        self.assertEqual(product_signature(self.row('롯데)펩시콜라600MLPACK2')).pack_count,2)

    def test_all_four_observed_labels_parse_volume_and_allow_manual_confirmation(self):
        names=('롯데)펩시콜라600MLPET','롯데)펩시콜라P600ml',
               '칠성)펩시콜라600ml','롯데)펩시콜라펫600ml')
        rows=[self.row(name) for name in names]
        for a in rows:
            self.assertEqual(product_signature(a).volume,('600','ml'))
            for b in rows:
                self.assertFalse(conflicting_mapping_variants(a,b))

    def test_500ml_and_600ml_still_conflict_when_pet_is_adjacent(self):
        a=self.row('롯데)펩시콜라600MLPET')
        b=self.row('칠성)펩시콜라500MLPET')
        self.assertTrue(conflicting_mapping_variants(a,b))
        self.assertTrue(conflicting_variants(a,b))

    def test_aliases_are_not_used_by_automatic_identity_checks(self):
        a=self.row('롯데)펩시콜라600ml',barcode='8801056193010')
        b=self.row('칠성)펩시콜라600ml',barcode='8801056193010')
        self.assertTrue(conflicting_variants(a,b))
        self.assertFalse(same_product(a,b))
        self.assertFalse(conflicting_mapping_variants(a,b))

    def test_aliases_do_not_generalize_to_other_products_or_pepsi_foods(self):
        for name in ('우유250ml','사이다600ml','펩시맛과자60g'):
            self.assertTrue(conflicting_mapping_variants(self.row('롯데)'+name),self.row('칠성)'+name)))

    def test_original_labels_and_explicit_metadata_remain_untouched(self):
        a=self.row('롯데)펩시콜라600MLPET',manufacturer='롯데',volume='600ml',flavor='오리지널')
        b=self.row('칠성)펩시콜라600ml',manufacturer='롯데칠성음료',volume='600ml',flavor='오리지널')
        original=[asdict(a),asdict(b)]
        self.assertFalse(conflicting_mapping_variants(a,b))
        self.assertEqual([asdict(a),asdict(b)],original)

    def test_flavor_and_pack_differences_still_block_manual_grouping(self):
        a=self.row('롯데)펩시콜라600MLPET')
        for name in ('칠성)펩시제로콜라600ml','칠성)펩시콜라라임600ml',
                     '칠성)펩시콜라600ml x2','칠성)펩시콜라600ml x3'):
            with self.subTest(name=name):
                self.assertTrue(conflicting_mapping_variants(a,self.row(name)))
        zero=self.row('롯데)펩시제로콜라라임600MLPET')
        self.assertTrue(conflicting_mapping_variants(zero,self.row('칠성)펩시제로콜라망고600ml')))
        self.assertTrue(conflicting_mapping_variants(replace(a,pack_count='2'),replace(a,pack_count='3')))
        self.assertTrue(conflicting_mapping_variants(self.row('롯데)펩시콜라600MLPACK2'),
                                                     self.row('칠성)펩시콜라600MLPACK3')))

    def test_maker_metadata_contradiction_outside_known_labels_still_blocks(self):
        a=self.row('롯데)펩시콜라600ml',manufacturer='다른회사')
        b=self.row('칠성)펩시콜라600ml')
        self.assertTrue(conflicting_mapping_variants(a,b))
