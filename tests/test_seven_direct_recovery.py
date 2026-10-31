"""공식 직접 보조의 실제 응답 재생·요청 상한·불확실성 회귀 검사."""
from pathlib import Path
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from core.cart_models import CatalogLink
from core.data.composite_inventory_adapter import CompositeInventoryAdapter
from core.data.live_mcp_adapter import LiveDataError


def response(payload, status=200):
    return SimpleNamespace(status_code=status, json=lambda: payload)


def metadata(code='P1'):
    return {'itemCd':code,'itemOnm':'공식 검증 음료600ml','onlinePrice':2500,
            'smCd':'M1','stokMngCd':'M1','stokMngQty':1,'stockApplicationRate':'100','evnMnmNm':'1+1'}


def store(code, offset=0):
    return {'storeCd':code,'storeNm':f'검증 {code}점','storeLat':str(37.5+offset),
            'storeLon':'127.0','addr1':'서울 관악구 주소','storeCloseYn':'N'}


def stock(code, quantity):
    return {'success':True,'data':{'smCd':'M1','storeList':[{'storeCd':code,'stock':quantity}]}}


class SevenDirectRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.link=CatalogLink('p','세븐일레븐','','P1','공식 검증 음료600ml')

    def adapter(self, stores=None):
        adapter=CompositeInventoryAdapter('세븐일레븐',emart_region=('서울특별시','관악구'))
        adapter.selected_stores=stores
        return adapter

    def test_real_three_stage_public_response_replay_preserves_source_boundaries(self):
        path=Path(__file__).resolve().parents[1]/'docs/research/fix-seven-direct-probes.json'
        evidence=json.loads(path.read_text(encoding='utf-8'))
        adapter=self.adapter()
        adapter.SEVEN_DIRECT_STORE_LIMIT=1
        link=CatalogLink('p','세븐일레븐','','8801056193010','칠성)펩시콜라600ml')
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(evidence[0]['response'])) as get, \
             patch('core.data.composite_inventory_adapter.requests.post',side_effect=[response(evidence[1]['response']),response(evidence[2]['response'])]) as post:
            rows=adapter.check_selected(link,37.458115,126.952161)
        self.assertEqual((get.call_count,post.call_count,adapter.request_count),(1,2,3))
        self.assertEqual((rows[0].store_code,rows[0].stock),('55457',20))
        self.assertEqual(rows[0].price,2500)
        self.assertEqual(rows[0].price_data_status,'REFERENCE')
        self.assertTrue(rows[0].stock_known)
        self.assertTrue(rows[0].stock_quantity_exact)
        self.assertFalse(rows[0].promotion_known)
        self.assertEqual(rows[0].stock_updated_at,'')
        self.assertEqual(rows[0].source_updated_at,'')
        self.assertTrue(rows[0].received_at)
        self.assertAlmostEqual(rows[0].distance_km,0.169456877068395)
        self.assertEqual(len(adapter.selected_stores),32)
        self.assertTrue(adapter.catalog_incomplete)
        self.assertIn('1개 점포',adapter.coverage_note)
        self.assertIn('32곳',adapter.coverage_note)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_direct_requests_nearest_three_stores_one_at_a_time(self):
        stores=[store(f'S{i}',i*.001) for i in range(5)]
        adapter=self.adapter()
        def post(url,**kwargs):
            if url.endswith('/search/store'):
                return response({'data':{'SearchQueryResult':{'Collection':[{'Documentset':{'Document':[
                    {'field':s} for s in reversed(stores)]}}]}}})
            codes=kwargs['json']['storeList']
            self.assertEqual(len(codes),1)
            return response(stock(codes[0],0))
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
             patch('core.data.composite_inventory_adapter.requests.post',side_effect=post) as posted:
            rows=adapter.check_selected(self.link,37.5,127)
        self.assertEqual([row.store_code for row in rows],['S0','S1','S2'])
        self.assertEqual(posted.call_count,4)
        self.assertEqual(adapter.request_count,5)
        self.assertTrue(all(row.stock==0 and row.stock_known for row in rows))
        self.assertTrue(adapter.catalog_incomplete)

    def test_cached_store_list_skips_new_store_search(self):
        adapter=self.adapter([store('S1')])
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
             patch('core.data.composite_inventory_adapter.requests.post',return_value=response(stock('S1',2))) as posted:
            rows=adapter.check_selected(self.link,37.5,127)
        self.assertEqual(posted.call_count,1)
        self.assertTrue(posted.call_args.args[0].endswith('/real-stock/multi/01/stocks'))
        self.assertEqual(adapter.request_count,2)
        self.assertEqual(rows[0].stock,2)

    def test_metadata_code_mismatch_stops_before_store_or_stock_request(self):
        adapter=self.adapter()
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata('WRONG'))), \
             patch('core.data.composite_inventory_adapter.requests.post') as posted:
            with self.assertRaises(LiveDataError):
                adapter.check_selected(self.link,37.5,127)
        posted.assert_not_called()
        self.assertEqual(adapter.request_count,1)

    def test_one_failed_store_does_not_become_zero_or_hide_other_success(self):
        adapter=self.adapter([store('S1'),store('S2',.001),store('S3',.002)])
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
             patch('core.data.composite_inventory_adapter.requests.post',side_effect=[
                 response(stock('S1',0)),response({},503),response(stock('S3',8))]):
            rows=adapter.check_selected(self.link,37.5,127)
        self.assertEqual([row.stock_known for row in rows],[True,False,True])
        self.assertEqual([row.stock for row in rows],[0,0,8])
        self.assertEqual(rows[1].stock_display,'미확인')
        self.assertEqual(rows[1].raw_payload['error']['diagnostics']['status'],503)
        self.assertEqual(adapter.lookup_failures,['S2'])
        self.assertTrue(adapter.catalog_incomplete)

    def test_access_denied_stops_remaining_stores_without_bypass_or_retry(self):
        adapter=self.adapter([store('S1'),store('S2',.001),store('S3',.002)])
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
             patch('core.data.composite_inventory_adapter.requests.post',return_value=response({},403)) as posted:
            with self.assertRaises(LiveDataError) as raised:
                adapter.check_selected(self.link,37.5,127)
        self.assertEqual(posted.call_count,1)
        self.assertEqual(adapter.request_count,2)
        self.assertEqual(len(raised.exception.details['inventory_results']),1)
        self.assertEqual(raised.exception.details['diagnostics']['status'],403)
        self.assertIn('중단',adapter.coverage_note)

    def test_negative_missing_or_conflicting_stock_is_unknown(self):
        for payload in (stock('S1',-1),stock('S1',None),{'data':{'smCd':'M1','storeList':[]}},
                        {'data':{'smCd':'M1','storeList':[{'storeCd':'S1','stock':0},{'storeCd':'S1','stock':5}]}}):
            with self.subTest(payload=payload):
                adapter=self.adapter([store('S1')])
                with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
                     patch('core.data.composite_inventory_adapter.requests.post',return_value=response(payload)):
                    with self.assertRaises(LiveDataError) as raised:
                        adapter.check_selected(self.link,37.5,127)
                self.assertEqual(raised.exception.details['error']['code'],'SEVEN_DIRECT_STOCK_FAILED')
                self.assertEqual(raised.exception.details['inventory_results'][0]['inventory'],payload)
                self.assertEqual(adapter.lookup_failures,['S1'])

    def test_all_stores_failing_keeps_metadata_stores_and_errors_then_raises(self):
        stores=[store('S1'),store('S2',.001),store('S3',.002)]
        adapter=self.adapter(stores)
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
             patch('core.data.composite_inventory_adapter.requests.post',return_value=response({},503)) as posted:
            with self.assertRaises(LiveDataError) as raised:
                adapter.check_selected(self.link,37.5,127)
        self.assertEqual(posted.call_count,3)
        self.assertEqual(adapter.selected_stores,stores)
        self.assertEqual(raised.exception.details['product']['itemCd'],'P1')
        self.assertEqual(len(raised.exception.details['stores']),3)
        self.assertEqual(len(raised.exception.details['errors']),3)
        self.assertEqual(adapter.lookup_failures,['S1','S2','S3'])

    def test_empty_valid_region_result_does_not_manufacture_stock(self):
        adapter=self.adapter()
        with patch('core.data.composite_inventory_adapter.requests.get',return_value=response(metadata())), \
             patch('core.data.composite_inventory_adapter.requests.post',return_value=response(
                 {'data':{'SearchQueryResult':{'Collection':[]}}})) as posted:
            rows=adapter.check_selected(self.link,37.5,127)
        self.assertEqual(rows,[])
        self.assertEqual(posted.call_count,1)
        self.assertEqual(adapter.lookup_failures,[])

    def test_metadata_access_denied_does_not_follow_redirects_or_request_other_stages(self):
        for status in (302,401,403,429):
            adapter=self.adapter()
            with self.subTest(status=status), \
                 patch('core.data.composite_inventory_adapter.requests.get',return_value=response({},status)), \
                 patch('core.data.composite_inventory_adapter.requests.post') as posted:
                with self.assertRaises(LiveDataError):
                    adapter.check_selected(self.link,37.5,127)
            self.assertEqual(adapter.request_count,1)
            posted.assert_not_called()
