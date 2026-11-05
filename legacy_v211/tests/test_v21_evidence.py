from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.models import StoreProduct, DataLoadResult
from core.cart_models import CartItem, CatalogLink
from core.services.query_service import fetch_snapshot
from core.services.v2_common import current_evidence
from core.services.integration_service import integrate_records
from core.ui.evidence import korean_time
from tests.live_fixtures import verified_offer


class SelectedManager:
    def __init__(self):
        self.calls=[]

    def search_selected(self, link, lat, lng, mode, **options):
        self.calls.append((link,lat,lng,mode,options))
        row=StoreProduct(link.brand,'테스트 점포','서울',link.product_name,
                         product_code=link.product_code,data_source='LIVE')
        return [row],[DataLoadResult(link.brand,[row],'PARTIAL','명시적 모의 누락')]


class EvidenceTests(unittest.TestCase):
    def row(self, **changes):
        now=datetime.now(timezone.utc).isoformat()
        row=StoreProduct('CU','테스트점','서울','검증 음료 250ml',barcode='8801',
                         price=1000,stock=3,promotion='1+1',data_source='LIVE',
                         price_data_status='LIVE',stock_data_status='LIVE',promotion_data_status='LIVE',
                         updated_at=now,received_at=now,stock_updated_at=now,
                         price_updated_at=now,promotion_updated_at=now,**verified_offer())
        return replace(row,**changes)

    def test_receipt_does_not_replace_missing_source_stock_time(self):
        row=self.row(stock_updated_at='',source_updated_at='',stock_source_updated_at='')
        self.assertFalse(current_evidence(row))
        self.assertEqual(korean_time(row.source_updated_at),'미제공')
        self.assertTrue(row.received_at)

    def test_expired_promotion_cannot_be_confirmed_even_with_new_receipt(self):
        end=(datetime.now(timezone(timedelta(hours=9))).date()-timedelta(days=1)).isoformat()
        row=self.row(promotion_end_date=end)
        self.assertFalse(current_evidence(row))
        self.assertIsNone(row.promotion_unit_price)

    def test_explicit_source_times_and_conditions_allow_live_fixture(self):
        self.assertTrue(current_evidence(self.row()))

    def test_selected_lookup_keeps_different_codes_despite_same_name(self):
        manager=SelectedManager()
        cart=[CartItem('a','음료',1),CartItem('b','음료',1)]
        links=[CatalogLink('a','CU','','001','같은 표시 이름'),
               CatalogLink('b','CU','','002','같은 표시 이름')]
        snapshot=fetch_snapshot(cart,links,manager,37.5,127,['CU'],'LIVE',True)
        self.assertEqual(len(manager.calls),2)
        self.assertEqual({call[0].product_code for call in manager.calls},{'001','002'})
        self.assertTrue(all(call[4]['force_refresh'] for call in manager.calls))
        self.assertTrue(all(row.data_source=='PARTIAL' for row in snapshot.records))

    def test_same_selected_product_is_only_queried_once_per_snapshot(self):
        manager=SelectedManager()
        cart=[CartItem('a','음료',1),CartItem('b','음료',2)]
        links=[CatalogLink(i,'CU','','001','같은 상품') for i in ('a','b')]
        fetch_snapshot(cart,links,manager,37.5,127,['CU'],'LIVE')
        self.assertEqual(len(manager.calls),1)

    def test_brand_catalog_price_is_reference_and_never_replaces_store_price(self):
        stock=self.row(price=0)
        catalog=replace(stock,store_name='',address='',store_code='',price=900,
                        price_data_status='LIVE')
        merged=integrate_records([stock],[catalog])[0]
        self.assertEqual(merged.price,900)
        self.assertEqual(merged.price_data_status,'REFERENCE')
        self.assertFalse(current_evidence(merged))
        self.assertEqual(integrate_records([self.row(price=1100)],[catalog])[0].price,1100)

    def test_naive_time_is_not_silently_relabelled_korean_time(self):
        self.assertIn('시간대 미제공',korean_time('2026-10-31T12:00:00'))
        self.assertIn('2026-11-01 00:00:00',korean_time('2026-10-31T15:00:00+00:00'))

    def test_stock_force_refresh_reuses_separate_brand_event_catalog(self):
        from core.data.data_manager import DataManager
        from core.data.promotion_manager import PromotionManager
        class Scraper:
            calls=0
            def search(self, keyword):
                self.calls+=1
                return [StoreProduct('CU','','','검증 음료',product_code='A',promotion='1+1')]
        scraper=Scraper()
        with TemporaryDirectory() as directory:
            root=Path(directory)
            promotions=PromotionManager(root/'events.json',scraper={'CU':scraper})
            manager=DataManager(root/'demo.csv','LIVE',cache_path=root/'stock.json',promotion_manager=promotions)
            def query(brand,name,lat,lng,link,force):
                return [self.row(product_code=link.product_code)],'',[],{}
            with patch.object(manager,'_query_inventory',side_effect=query) as inventory:
                for code in ('A','B'):
                    manager.search_selected(CatalogLink(code,'CU','',code,'검증 음료'),37,127,force_refresh=True)
            self.assertEqual(inventory.call_count,2)
            self.assertEqual(scraper.calls,1)
