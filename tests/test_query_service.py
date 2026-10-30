import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from core.models import StoreProduct, DataLoadResult
from core.cart_models import CartItem, CatalogLink
from core.data.data_manager import DataManager
from core.services.query_service import fetch_snapshot, new_session_manager


class QueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.promo=Mock();self.promo.search.return_value=[]

    def manager(self):
        return DataManager(self.root/'test.csv','LIVE',cache_path=self.root/'cache.json',
                           cache_ttl_seconds=300,promotion_manager=self.promo)

    def test_unpriced_rows_optional_and_ttl_refresh(self):
        manager=self.manager()
        row=StoreProduct('CU','점포','주소','음료',price=0,stock=2,store_code='A')
        with patch.object(manager,'_search_inventory_tracks',return_value=[row]) as query:
            with patch('core.data.data_manager.time.time',return_value=1000):
                self.assertEqual(manager.search_all('음료',37,127,['CU'])[0],[])
            with patch('core.data.data_manager.time.time',return_value=1200):
                self.assertEqual(len(manager.search_all('음료',37,127,['CU'],include_unpriced=True)[0]),1)
                self.assertEqual(query.call_count,1)
            with patch('core.data.data_manager.time.time',return_value=1301):
                manager.search_all('음료',37,127,['CU'],include_unpriced=True)
                self.assertEqual(query.call_count,2)

    def test_force_refresh_and_cache_failure(self):
        manager=self.manager()
        row=StoreProduct('CU','A','주소','음료',price=1000,stock=2,store_code='A')
        with patch.object(manager,'_search_inventory_tracks',return_value=[row]) as query:
            manager.search_all('음료',37,127,['CU'])
            manager.search_all('음료',37,127,['CU'],force_refresh=True)
            self.assertEqual(query.call_count,2)
        with patch.object(manager,'_search_inventory_tracks',side_effect=RuntimeError('offline')):
            rows,statuses=manager.search_all('음료',37,127,['CU'],force_refresh=True)
        self.assertEqual(statuses[0].source,'CACHE')
        self.assertEqual(rows[0].data_source,'CACHE')

    def test_partial_is_not_upgraded_by_snapshot(self):
        row=StoreProduct('CU','A','주소','음료',barcode='8801',price=1800,stock=3,store_code='A',data_source='LIVE')
        manager=Mock()
        manager.search_all.return_value=([row],[DataLoadResult('CU',[row],'PARTIAL','일부 경로 실패')])
        snapshot=fetch_snapshot([CartItem('d','음료',2)],[CatalogLink('d','CU','8801','','음료')],manager,37,127,['CU'],'LIVE')
        self.assertEqual(snapshot.records[0].data_source,'PARTIAL')
        self.assertEqual(row.data_source,'LIVE')
        self.assertEqual(snapshot.statuses_by_item['d'][0].source,'PARTIAL')

    def test_session_paths_are_distinct(self):
        a,dir_a=new_session_manager('TEST');b,dir_b=new_session_manager('TEST')
        self.addCleanup(dir_a.cleanup);self.addCleanup(dir_b.cleanup)
        self.assertNotEqual(a.cache_path,b.cache_path)
        self.assertNotEqual(a.promotion_manager.cache_path,b.promotion_manager.cache_path)
