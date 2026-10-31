"""2026-10-31 실제 공개 응답의 구조를 재현한 모의 자료. 외부 요청 없음."""
from copy import deepcopy
from unittest import TestCase

from core.cart_models import CatalogLink
from core.data.live_mcp_adapter import DaisoMcpAdapter
from core.services.catalog import matches_link


class Reply:
    def __init__(self, payload):
        self.payload=payload
        self.calls=[]

    def call(self, tool, arguments):
        self.calls.append((tool,arguments))
        return deepcopy(self.payload)


class CuEmartReplayTests(TestCase):
    def test_cu_photo_code_resolves_and_missing_source_time_stays_missing(self):
        code='8801056193010'
        payload={'inventory':{'available':True,'items':[
            {'itemCode':code,'itemName':'롯데)펩시콜라P600ml','price':2500}]},
            'nearbyStores':{'available':True,'stockItemCode':code,'stores':[
                {'storeCode':'C1','storeName':'구조 검증점','address':'서울 검증 주소',
                 'latitude':37.457726,'longitude':126.948605,'distanceM':317,'stock':13}]}}
        fake=Reply(payload)
        link=CatalogLink('p','CU',code,code,'롯데)펩시콜라P600ml')
        rows=DaisoMcpAdapter('CU',mcp_client=fake).check_selected(link,37.458115,126.952161)
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(len(rows),1)
        self.assertTrue(matches_link(rows[0],link))
        self.assertEqual(rows[0].stock,13)
        self.assertTrue(rows[0].stock_known)
        self.assertTrue(rows[0].stock_quantity_exact)
        self.assertEqual(rows[0].price,2500)
        self.assertEqual(rows[0].price_data_status,'REFERENCE')
        self.assertTrue(rows[0].received_at)
        self.assertEqual(rows[0].source_updated_at,'')
        self.assertEqual(rows[0].stock_updated_at,'')

    def test_emart_stock_rows_join_only_same_store_code_coordinates(self):
        code='8801056175955'
        payload={'pluCd':code,'selectionReason':'explicit_code',
            'nearbyStores':{'stores':[
                {'storeCode':'E1','storeName':'검증 A점','address':'서울 검증 A',
                 'latitude':37.4728,'longitude':126.9352,'distanceM':2212},
                {'storeCode':'E2','storeName':'검증 B점','address':'서울 검증 B',
                 'latitude':37.4741,'longitude':126.9686,'distanceM':2305},
                {'storeCode':'E3','storeName':'수량 미제공점','address':'서울 검증 C',
                 'latitude':37.47,'longitude':126.96,'distanceM':2400}]},
            'inventory':{'goodsInfo':{'pluCd':code,'goodsNm':'롯데)펩시콜라제로펫500ml','viewPrice':2500},
                         'stores':[{'bizNo':'E1','bizQty':0,'storeName':'검증 A점','distanceM':2212},
                                   {'bizNo':'E2','bizQty':15,'storeName':'검증 B점','distanceM':2305}]}}
        fake=Reply(payload)
        link=CatalogLink('p','이마트24','',code,'롯데)펩시콜라제로펫500ml')
        adapter=DaisoMcpAdapter('이마트24',mcp_client=fake,emart_region=('서울특별시','관악구'))
        rows=adapter.check_selected(link,37.458115,126.952161)
        by_code={row.store_code:row for row in rows}
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(set(by_code),{'E1','E2'})
        self.assertEqual((by_code['E1'].latitude,by_code['E1'].longitude),(37.4728,126.9352))
        self.assertEqual((by_code['E2'].latitude,by_code['E2'].longitude),(37.4741,126.9686))
        self.assertEqual(by_code['E1'].stock,0)
        self.assertTrue(by_code['E1'].stock_known)
        self.assertEqual(by_code['E2'].stock,15)
        self.assertTrue(all(row.price==2500 and row.price_data_status=='REFERENCE' for row in rows))
        self.assertTrue(all(row.stock_updated_at=='' for row in rows))
