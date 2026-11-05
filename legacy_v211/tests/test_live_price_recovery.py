"""공식 목록의 확인 가격은 보존하되 현재 점포 가격이나 행사로 승격하지 않는다."""
from dataclasses import replace
from datetime import datetime
import unittest

from core.models import StoreProduct
from core.services.integration_service import integrate_records
from core.services.product_matching import same_product, same_catalog_reference
from core.services.product_list import load_photo_catalog
from core.services.cart_pricing import evaluate_line
from core.cart_models import CartItem
from core.services.v2_common import current_evidence
from tests.live_fixtures import verified_offer


class LiveReferencePriceRecoveryTests(unittest.TestCase):
    def stock(self, name='나뚜루)녹차파인트', **changes):
        row=StoreProduct('CU','확인점','서울',name,product_code='inventory-17',
                         price=0,stock=4,data_source='LIVE',promotion_known=False,
                         stock_known=True,stock_data_status='LIVE')
        return replace(row,**changes)

    def catalog(self, name='나뚜루)녹차파인트', **changes):
        row=StoreProduct('CU','','',name,product_code='8801062451739',price=15900,
                         promotion='1+1',promotion_known=True,data_source='LIVE',
                         price_source='CU 공식 행사 페이지',promotion_source='CU 공식 행사 페이지',
                         price_data_status='REFERENCE',promotion_data_status='LIVE',
                         promotion_checked_at='2026-10-31T10:00:00+09:00',
                         raw_payload={'identifier_source':'official_image_asset'})
        return replace(row,**changes)

    def test_exact_catalog_name_retains_reference_price_without_changing_identity(self):
        stock,event=self.stock(),self.catalog()
        self.assertFalse(same_product(stock,event))
        self.assertTrue(same_catalog_reference(stock,event))
        row=integrate_records([stock],[event])[0]
        self.assertEqual(row.product_code,'inventory-17')
        self.assertEqual(row.price,15900)
        self.assertEqual(row.price_data_status,'REFERENCE')
        self.assertEqual(row.price_source,event.price_source)
        self.assertEqual(row.stock,4)
        self.assertFalse(row.promotion_known)
        self.assertEqual(row.promotion,'')
        self.assertEqual(row.raw_payload['reference_price_match'],'exact_name_reference_only')
        self.assertEqual(evaluate_line(CartItem('x',row.product_name,1),row).pricing_state,'REFERENCE')

    def test_different_volume_flavor_package_and_brand_do_not_supply_reference_price(self):
        stock=self.stock('회사)딸기우유250ml x2')
        for name in ('회사)딸기우유500ml x2','회사)초코우유250ml x2','회사)딸기우유250ml x3',
                     '회사)딸기우유250ml','다른회사)딸기우유250ml x2'):
            with self.subTest(name=name):
                self.assertEqual(integrate_records([stock],[self.catalog(name)])[0].price,0)
        self.assertEqual(integrate_records([stock],[self.catalog(stock.product_name,brand='GS25')])[0].price,0)

    def test_unmarked_conflicting_internal_codes_are_not_assumed_image_codes(self):
        row=integrate_records([self.stock()],[self.catalog(raw_payload={})])[0]
        self.assertEqual(row.price,0)

    def test_conflicting_explicit_barcodes_block_name_only_price_association(self):
        stock=self.stock(barcode='8801007021652')
        row=integrate_records([stock],[self.catalog(barcode='8801007021653')])[0]
        self.assertEqual(row.price,0)

    def test_same_identity_different_unverified_promotions_keep_price_but_not_best_offer(self):
        stock=self.stock(product_code='P')
        one=self.catalog(product_code='P')
        two=replace(one,promotion='2+1')
        row=integrate_records([stock],[one,two])[0]
        self.assertEqual(row.price,15900)
        self.assertEqual(row.price_data_status,'REFERENCE')
        self.assertFalse(row.promotion_known)
        self.assertEqual(len(row.raw_payload['reference_price_candidates']),2)

    def test_conflicting_catalog_prices_are_preserved_as_candidates_not_minimized(self):
        stock=self.stock()
        rows=[self.catalog(price=15900),self.catalog(price=16000)]
        result=integrate_records([stock],rows)[0]
        self.assertEqual(result.price,0)
        self.assertEqual({r['price'] for r in result.raw_payload['reference_price_candidates']},{15900,16000})

    def test_current_store_price_survives_reference_and_promotion_conflict(self):
        stock=self.stock(price=17000,price_data_status='LIVE',promotion='2+1',promotion_known=True)
        row=integrate_records([stock],[self.catalog(product_code=stock.product_code)])[0]
        self.assertEqual(row.price,17000)
        self.assertEqual(row.price_data_status,'LIVE')
        self.assertEqual(row.promotion,'2+1')

    def test_photo_catalog_keeps_reference_identity_provenance(self):
        for row in load_photo_catalog():
            self.assertEqual(row.raw_payload['identifier_source'],'official_image_asset')
            self.assertTrue(row.raw_payload['catalog_only'])
            self.assertEqual(row.price_data_status,'REFERENCE')
            self.assertFalse(row.stock_known)

    def test_same_offer_label_does_not_replace_store_member_conditions_or_clocks(self):
        now=datetime.now().astimezone().isoformat()
        stock=self.stock(product_code='P',price=1800,promotion='1+1',promotion_known=True,
                         promotion_source='점포별 응답',price_data_status='LIVE',
                         promotion_data_status='LIVE',stock_updated_at=now,source_updated_at=now,
                         stock_source_updated_at=now,price_updated_at=now,promotion_updated_at=now,
                         received_at=now,promotion_checked_at=now,**verified_offer())
        stock=replace(stock,promotion_member_condition='VIP 회원 전용')
        catalog=replace(stock,store_name='',address='',store_code='',price_data_status='REFERENCE',
                        promotion_member_condition='없음',promotion_source='브랜드 공통 목록',
                        promotion_checked_at='2000-01-01T00:00:00+09:00')
        self.assertFalse(current_evidence(stock))
        joined=integrate_records([stock],[catalog])[0]
        self.assertEqual(joined.promotion_member_condition,'VIP 회원 전용')
        self.assertEqual(joined.promotion_source,'점포별 응답')
        self.assertEqual(joined.promotion_checked_at,now)
        self.assertEqual(joined.promotion_updated_at,now)
        self.assertFalse(current_evidence(joined))
        self.assertIsNone(evaluate_line(CartItem('x',joined.product_name,2),joined).payable)

    def test_previous_brand_catalog_offer_is_not_misclassified_as_original_store_offer(self):
        stock=self.stock(product_code='P')
        first=self.catalog(product_code='P',promotion='1+1')
        joined=integrate_records([stock],[first])[0]
        updated=self.catalog(product_code='P',promotion='2+1')
        joined_again=integrate_records([joined],[updated])[0]
        self.assertEqual(joined_again.promotion,'2+1')
        self.assertEqual(joined_again.price_data_status,'REFERENCE')
