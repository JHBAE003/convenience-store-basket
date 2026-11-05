from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core.data.data_manager import DataManager
from core.data.mcp_client import McpRequestError
from core.data.live_mcp_adapter import LiveDataError
from core.cart_models import CatalogLink
from core.models import StoreProduct


class OfficialStoreCacheTests(unittest.TestCase):
    def test_official_region_list_is_reused_per_exact_location_and_ttl(self):
        fetched=[]
        class Primary:
            request_count=1
            def __init__(self,brand): pass
            def check_selected(self,*args):
                raise McpRequestError('중계 502',{'diagnostics':{'status':502}})
        class Official:
            request_count=3
            lookup_failures=()
            catalog_incomplete=True
            coverage_note='공식 보조: 가까운 3개 점포 확인'
            def __init__(self,brand): self.selected_stores=None
            def check_selected(self,link,lat,lng):
                if self.selected_stores is None:
                    fetched.append((lat,lng))
                    self.selected_stores=[{'field_id':'official','storeNo':'S'}]
                return [StoreProduct('세븐일레븐','공식 점포','서울','음료',product_code=link.product_code,
                                     stock=3,store_code='S',latitude=lat,longitude=lng,data_source='LIVE')]
        class Events:
            def search(self,*args): return []
        with TemporaryDirectory() as directory:
            root=Path(directory)
            manager=DataManager(root/'demo.csv','LIVE',cache_path=root/'cache.json',promotion_manager=Events())
            manager._store_cache[('세븐일레븐',37.5,127)]=(1000,[{'field_id':'different_mcp_source'}])
            with patch('core.data.data_manager.DaisoMcpAdapter',Primary), \
                 patch('core.data.data_manager.CompositeInventoryAdapter',Official), \
                 patch.object(manager,'_resolve_region',return_value=('서울특별시','관악구')):
                for code,lat,stamp in [('A',37.5,1000),('B',37.5,1061),('C',37.5001,1062),('D',37.5,2801)]:
                    with patch('core.data.data_manager.time.time',return_value=stamp):
                        rows,statuses=manager.search_selected(CatalogLink(code,'세븐일레븐','',code,'음료'),lat,127)
                        self.assertEqual(rows[0].stock,3)
                        self.assertEqual(statuses[0].source,'PARTIAL')
                        self.assertIn('가까운 3개',statuses[0].message)
        self.assertEqual(fetched,[(37.5,127),(37.5001,127),(37.5,127)])

    def test_valid_store_list_survives_all_stock_failure_without_becoming_stock_success(self):
        fetched=[]
        class Primary:
            request_count=1
            def __init__(self,brand): pass
            def check_selected(self,*args): raise LiveDataError('중계 실패')
        class Official:
            request_count=3
            def __init__(self,brand): self.selected_stores=None
            def check_selected(self,*args):
                if self.selected_stores is None:
                    fetched.append(True)
                    self.selected_stores=[{'storeCd':'S'}]
                raise LiveDataError('선택 점포 전체 실패')
        class Events:
            def search(self,*args): return []
        with TemporaryDirectory() as directory:
            root=Path(directory)
            manager=DataManager(root/'demo.csv','LIVE',cache_path=root/'cache.json',promotion_manager=Events())
            with patch('core.data.data_manager.DaisoMcpAdapter',Primary), \
                 patch('core.data.data_manager.CompositeInventoryAdapter',Official), \
                 patch.object(manager,'_resolve_region',return_value=('서울특별시','관악구')):
                for code,stamp in [('A',1000),('B',1061)]:
                    with patch('core.data.data_manager.time.time',return_value=stamp):
                        rows,statuses=manager.search_selected(CatalogLink(code,'세븐일레븐','',code,'음료'),37.5,127)
                        self.assertEqual(rows,[])
                        self.assertEqual(statuses[0].source,'ERROR')
            self.assertEqual(manager._store_cache[('seven_official',37.5,127,'서울특별시','관악구')][0],1000)
        self.assertEqual(len(fetched),1)
