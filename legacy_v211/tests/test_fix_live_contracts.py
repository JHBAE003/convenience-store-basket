"""실제 GS25 검증 오류와 분리된 재고/점포 응답의 회귀 검사."""
from dataclasses import replace
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from core.cart_models import CatalogLink
from core.data.live_mcp_adapter import DaisoMcpAdapter, LiveDataError
from core.data.composite_inventory_adapter import CompositeInventoryAdapter
from core.data.mcp_client import McpClient, McpRequestError
from core.models import StoreProduct


class FakeSession:
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def post(self, *_args, **_kwargs):
        payload = self.payload
        class Response:
            status_code = 200
            headers = {'content-type': 'application/json'}
            text = json.dumps(payload)

            def json(self):
                return payload

            def raise_for_status(self):
                pass
        return Response()


class LiveContractRepairTests(unittest.TestCase):
    def test_gs25_selected_code_also_supplies_required_keyword_without_discovery(self):
        class ValidatingMcp:
            calls = []

            def call(self, tool, arguments):
                self.calls.append((tool, arguments))
                if not isinstance(arguments.get('keyword'), str):
                    raise McpRequestError('keyword 필수')
                return {'itemCode': 'G1', 'inventory': {'stores': [
                    {'storeCode': 'S1', 'storeName': '점포', 'realStockQuantity': 0}]}}
        client = ValidatingMcp()
        link = CatalogLink('x', 'GS25', '', 'G1', '검증 음료250ml')
        rows = DaisoMcpAdapter('GS25', mcp_client=client).check_selected(link, 37.5, 127)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], 'check_inventory')
        self.assertEqual(client.calls[0][1]['itemCode'], 'G1')
        self.assertEqual(client.calls[0][1]['keyword'], link.product_name)
        self.assertEqual(rows[0].stock, 0)
        self.assertTrue(rows[0].stock_known)

    def test_gs25_rest_recovery_keeps_code_and_keyword_without_product_search(self):
        calls=[]
        def rest(_adapter,path,arguments):
            calls.append((path,arguments))
            if 'keyword' not in arguments:
                raise LiveDataError('선택 상품명 필수')
            return {'inventory': {'stores': [
                {'storeCode':'S1','storeName':'보조 점포','realStockQuantity':0}]}}
        link=CatalogLink('x','GS25','','G1','검증 음료250ml')
        with patch.object(DaisoMcpAdapter,'_rest',autospec=True,side_effect=rest):
            rows=CompositeInventoryAdapter('GS25').check_selected(link,37.5,127)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0],'/api/gs25/inventory')
        self.assertEqual(calls[0][1]['itemCode'],'G1')
        self.assertEqual(calls[0][1]['keyword'],link.product_name)
        self.assertEqual(rows[0].product_code,'G1')
        self.assertTrue(rows[0].stock_known)
        self.assertEqual(rows[0].stock,0)

    def test_seven_rest_recovery_keeps_same_region_as_primary(self):
        calls=[]
        def direct(method,path,**arguments):
            calls.append((method,path,arguments))
            if method=='GET':
                return {'itemCd':'P1','itemOnm':'검증 음료600ml','smCd':'P1',
                        'stokMngCd':'P1','stokMngQty':1,'stockApplicationRate':'100'}
            if path.endswith('/search/store'):
                return {'data':{'SearchQueryResult':{'Collection':[{'Documentset':{'Document':[
                    {'field':{'storeCd':'S1','storeNm':'관악점','storeLat':'37.4582','storeLon':'126.9522'}}]}}]}}}
            return {'data':{'smCd':'P1','storeList':[{'storeCd':'S1','stock':3}]}}
        link=CatalogLink('x','세븐일레븐','','P1','검증 음료600ml')
        adapter=CompositeInventoryAdapter('세븐일레븐',emart_region=('서울특별시','관악구'))
        with patch.object(adapter,'_seven_json',side_effect=direct), \
             patch('core.data.live_mcp_adapter.emart24_region_korean') as geocoder:
            rows=adapter.check_selected(link,37.458115,126.952161)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[1][1],'/api/v1/open/search/store')
        self.assertEqual(calls[1][2]['body']['query'],'서울 관악구')
        self.assertEqual(calls[0][2]['params']['itemCd'],'P1')
        self.assertEqual(rows[0].stock,3)
        geocoder.assert_not_called()

    def test_sdk_json_array_validation_error_retains_original_path_and_message(self):
        validation = [{'expected': 'string', 'code': 'invalid_type', 'path': ['keyword'],
                       'message': 'Invalid input: expected string, received undefined'}]
        envelope = {'jsonrpc': '2.0', 'result': {'isError': True,
            'content': [{'type': 'text', 'text': json.dumps(validation)}]}}
        client = McpClient(session=FakeSession(envelope), retries=0, min_interval=0)
        with self.assertRaises(McpRequestError) as raised:
            client.call('check_inventory', {'service': 'gs25', 'itemCode': 'G1'})
        self.assertEqual(raised.exception.details['validation_errors'], validation)
        self.assertEqual(raised.exception.details['error']['code'], 'INVALID_ARGUMENTS')
        self.assertIn('expected string', str(raised.exception))
        self.assertEqual(client.request_count, 1)

    def test_jsonrpc_error_preserves_nested_details(self):
        error = {'code': -32602, 'message': '선택 조건 오류', 'data': {'field': 'keyword'}}
        client = McpClient(session=FakeSession({'jsonrpc': '2.0', 'error': error}), retries=0, min_interval=0)
        with self.assertRaises(McpRequestError) as raised:
            client.call('check_inventory', {'service': 'gs25'})
        self.assertEqual(raised.exception.details['error'], error)

    def test_seven_stock_unavailable_is_not_normal_empty_success(self):
        class FakeMcp:
            def call(self, *_args):
                return {'stockAvailable': False, 'stockError': '원본 재고 일시 중단',
                        'inventory': {'stores': []}}
        adapter = DaisoMcpAdapter('세븐일레븐', mcp_client=FakeMcp(), emart_region=('서울특별시', '중구'))
        with self.assertRaises(LiveDataError) as raised:
            adapter.check_selected(CatalogLink('s', '세븐일레븐', '', 'S1', '상품'), 37.5, 127)
        self.assertIn('원본 재고 일시 중단', str(raised.exception))
        self.assertFalse(raised.exception.details['response']['stockAvailable'])

    def test_store_location_join_uses_exact_code_and_adds_no_stock_rows(self):
        row = StoreProduct('이마트24', '같은 매장명', '', '음료', store_code='E1', stock=0)
        payload = {'nearbyStores': {'stores': [
            {'storeCode': 'E2', 'storeName': '같은 매장명', 'latitude': 37.4, 'longitude': 126.9},
            {'storeCode': 'E1', 'storeName': '정확 코드', 'latitude': 37.5, 'longitude': 127.0}]}}
        found = DaisoMcpAdapter._enrich_store_locations(payload, [row], 37.5, 127.0)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].latitude, found[0].longitude), (37.5, 127.0))
        self.assertEqual(found[0].stock, 0)
        self.assertEqual(found[0].distance_km, 0)

    def test_conflicting_location_metadata_is_not_arbitrarily_chosen(self):
        row = StoreProduct('이마트24', '점포', '', '음료', store_code='E1')
        payload = {'nearbyStores': {'stores': [
            {'storeCode': 'E1', 'latitude': 37.4, 'longitude': 126.9},
            {'storeCode': 'E1', 'latitude': 37.5, 'longitude': 127.0}]}}
        found = DaisoMcpAdapter._enrich_store_locations(payload, [row], 37.5, 127.0)
        self.assertIsNone(found[0].latitude)
        self.assertIsNone(found[0].longitude)
