"""느린 전체 행사 목록은 일반 재고 조회를 막지 않고 명시적 확인으로만 수집한다."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import json
import time
import threading
import unittest

from core.data.promotion_manager import PromotionManager
from core.models import StoreProduct
from core.scrapers.public_event_scraper import CuPromotionScraper, SevenElevenPromotionScraper


class Scraper:
    def __init__(self):
        self.calls=[]
        self.last_collection_complete=True

    def search(self, keyword):
        self.calls.append(keyword)
        return [StoreProduct('CU','','','콜라250ml',price=1800,promotion='1+1',
                             price_data_status='REFERENCE',data_source='LIVE',
                             raw_payload={'collection_complete':self.last_collection_complete})]


class DeferredCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'events.json'
        self.scraper=Scraper()

    def manager(self, **kwargs):
        return PromotionManager(self.path,{'CU':self.scraper},defer_catalog=True,**kwargs)

    def test_cold_catalog_is_deferred_even_when_stock_force_is_requested(self):
        manager=self.manager()
        for force in (False,True):
            self.assertEqual(manager.search('CU','콜라',force=force),[])
            self.assertEqual(manager.status_for('CU','콜라')['status'],'UNKNOWN')
            self.assertIn('추가 확인 버튼',manager.status_for('CU','콜라')['message'])
        self.assertEqual(self.scraper.calls,[])

    def test_expired_catalog_is_not_refetched_or_relabelled_during_stock_lookup(self):
        row=self.scraper.search('')[0]
        self.scraper.calls.clear()
        self.path.write_text(json.dumps({'CU|*':{'saved_at':time.time()-1000,'records':[asdict(row)]}}),encoding='utf-8')
        manager=self.manager(ttl_seconds=60)
        self.assertEqual(manager.search('CU','콜라'),[])
        self.assertEqual(self.scraper.calls,[])
        self.assertEqual(manager.status_for('CU','콜라')['status'],'UNKNOWN')

    def test_valid_cached_catalog_is_reused_without_explicit_network_permission(self):
        row=self.scraper.search('')[0]
        self.scraper.calls.clear()
        self.path.write_text(json.dumps({'CU|*':{'saved_at':time.time(),'records':[asdict(row)]}}),encoding='utf-8')
        manager=self.manager()
        found=manager.search('CU','콜라')
        self.assertEqual([r.price for r in found],[1800])
        self.assertTrue(found[0].app_cache_used)
        self.assertEqual(self.scraper.calls,[])
        self.assertEqual(manager.status_for('CU','콜라')['status'],'CACHE')

    def test_explicit_catalog_permission_fetches_once_and_reuses_filtered_result(self):
        manager=self.manager()
        self.assertEqual(manager.search('CU','콜라'),[])
        found=manager.search('CU','콜라',allow_catalog_fetch=True)
        self.assertEqual(len(found),1)
        self.assertEqual(self.scraper.calls,[''])
        self.assertEqual(manager.search('CU','다른상품'),[])
        self.assertEqual(self.scraper.calls,[''])

    def test_partial_catalog_remains_partial_in_cache_and_when_there_are_no_matches(self):
        self.scraper.last_collection_complete=False
        manager=self.manager()
        manager.search('CU','콜라',allow_catalog_fetch=True)
        self.assertEqual(manager.status_for('CU','콜라')['status'],'PARTIAL')
        self.assertEqual(manager.search('CU','없는상품'),[])
        self.assertEqual(manager.status_for('CU','없는상품')['status'],'PARTIAL')
        self.assertEqual(self.scraper.calls,[''])

    def test_default_legacy_manager_still_fetches_without_new_permission(self):
        manager=PromotionManager(self.path,{'CU':self.scraper})
        self.assertEqual(len(manager.search('CU','콜라')),1)
        self.assertEqual(self.scraper.calls,[''])

    def test_deferred_default_scrapers_limit_each_explicit_brand_lookup_to_four_requests(self):
        manager=PromotionManager(self.path,defer_catalog=True)
        self.assertEqual(manager.scrapers['CU'].max_pages,2)
        self.assertEqual(manager.scrapers['세븐일레븐'].max_pages,1)
        self.assertEqual(CuPromotionScraper().max_pages,30)
        self.assertEqual(SevenElevenPromotionScraper().max_pages,10)

    def test_cold_inventory_query_does_not_start_catalog_http_collection(self):
        from core.data.data_manager import DataManager
        from core.cart_models import CatalogLink
        manager=DataManager(Path(self.directory.name)/'demo.csv','LIVE',
                            cache_path=Path(self.directory.name)/'stock.json',promotion_manager=self.manager())
        row=StoreProduct('CU','확인점','서울','콜라250ml',product_code='P',stock=4,data_source='LIVE')
        with patch.object(manager,'_query_inventory',return_value=([row],'',[],{})), \
             patch('requests.sessions.Session.request',side_effect=AssertionError('외부 HTTP 금지')) as http:
            found,_=manager.search_selected(CatalogLink('x','CU','','P','콜라250ml'),37.5,127)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0].stock,4)
        self.assertEqual(self.scraper.calls,[])
        http.assert_not_called()

    def test_background_explicit_catalog_does_not_block_normal_inventory_lookup(self):
        entered,release=threading.Event(),threading.Event()
        base=self.scraper
        class Slow:
            def search(self,keyword):
                entered.set()
                release.wait(2)
                return base.search(keyword)
        manager=PromotionManager(self.path,{'CU':Slow()},defer_catalog=True)
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(manager.search,'CU','콜라',allow_catalog_fetch=True)
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(manager.search('CU','다른상품'),[])
                self.assertEqual(manager.status_for('CU','다른상품')['status'],'UNKNOWN')
            finally:
                release.set()
            self.assertEqual(len(future.result(2)),1)


class PageLimitTests(unittest.TestCase):
    def test_cu_caps_at_four_posts_and_marks_the_whole_returned_scope_partial(self):
        class Response:
            status_code=200
            content=b'<li class="prod_list"><p class="name">cola</p><strong class="price">1800</strong></li><div class="prodListBtn">next</div>'
            def raise_for_status(self):pass
        class Session:
            headers={}
            calls=0
            def post(self,*args,**kwargs):
                self.calls+=1
                return Response()
        session=Session()
        scraper=CuPromotionScraper(session=session,max_pages=2)
        with patch('core.scrapers.public_event_scraper.time.sleep'):
            rows=scraper.search('cola')
        self.assertEqual(session.calls,4)
        self.assertFalse(scraper.last_collection_complete)
        self.assertTrue(rows)
        self.assertTrue(all(r.raw_payload['collection_complete'] is False for r in rows))

    def test_seven_caps_at_four_posts_and_marks_scope_partial(self):
        class Response:
            status_code=200
            content=b'<div class="pic_product"><div class="name">cola</div><div class="price">1800</div></div>'
            def raise_for_status(self):pass
        class Session:
            headers={}
            calls=0
            def post(self,*args,**kwargs):
                self.calls+=1
                return Response()
        session=Session()
        scraper=SevenElevenPromotionScraper(session=session,max_pages=1)
        with patch('core.scrapers.public_event_scraper.time.sleep'):
            rows=scraper.search('cola')
        self.assertEqual(session.calls,4)
        self.assertFalse(scraper.last_collection_complete)
        self.assertTrue(rows)
        self.assertTrue(all(r.raw_payload['collection_complete'] is False for r in rows))
