from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from core.models import StoreProduct, DataLoadResult
from core.cart_models import CartItem, CatalogLink
from core.services.query_service import fetch_snapshot
from core.services.basket import evaluate_stores
from core.services.v2_common import apply_current_evidence


class LiveVisibilityTests(unittest.TestCase):
    def row(self,**changes):
        row=StoreProduct('CU','응답 점포','서울','펩시600ml',product_code='P',price=2500,stock=13,
                         latitude=37.5,longitude=127,data_source='LIVE',stock_data_status='LIVE',
                         price_data_status='REFERENCE',promotion_known=False,
                         received_at=datetime.now(timezone.utc).isoformat())
        return replace(row,**changes)

    def test_unlinked_brand_is_explicitly_not_queried(self):
        manager=Mock()
        row=self.row()
        manager.search_all.return_value=([row],[DataLoadResult('CU',[row],'LIVE','정상')])
        snapshot=fetch_snapshot([CartItem('i','펩시600ml',1)],[CatalogLink('i','CU','','P','펩시600ml')],
                                manager,37.5,127,['CU','GS25'],'LIVE')
        self.assertEqual({status.brand:status.source for status in snapshot.statuses},{'CU':'LIVE','GS25':'UNLINKED'})
        manager.search_all.assert_called_once()

    def test_missing_price_or_promo_does_not_erase_valid_stock_evaluation(self):
        from tests.live_fixtures import verified_offer
        now=datetime.now(timezone.utc).isoformat()
        row=self.row(stock_updated_at=now,source_updated_at=now,stock_source_updated_at=now,
                     promotion='1+1',promotion_known=True,**verified_offer())
        store=evaluate_stores([CartItem('i','펩시600ml',1)],{'i':[row]},37.5,127,2)[0]
        guarded=apply_current_evidence([store])[0]
        self.assertFalse(guarded.eligible)
        self.assertEqual(guarded.lines[0].stock_state,'ENOUGH')
        self.assertIsNone(guarded.total)

    def test_received_stock_and_reference_price_remain_visible_without_origin_clock(self):
        from core.services.observations import observation_rows
        row=self.row()
        result=observation_rows([row],37.5,127,2)[0]
        self.assertIn('13개',result['재고 응답'])
        self.assertIn('2,500',result['판매가'])
        self.assertEqual(result['가격 구분'],'상품 목록 참고가')
        self.assertEqual(result['원본 재고 시각'],'미제공')
        self.assertNotEqual(result['앱 수신 시각'],'미제공')

    def test_outside_radius_is_visible_without_becoming_a_recommendation(self):
        from core.services.observations import observation_rows
        row=self.row(latitude=37.522,longitude=127)
        result=observation_rows([row],37.5,127,2)[0]
        self.assertEqual(result['검색 범위'],'반경 밖')
        stores=evaluate_stores([CartItem('i','펩시600ml',1)],{'i':[row]},37.5,127,2)
        self.assertEqual(stores,[])

    def test_zero_quantity_unknown_and_failure_have_different_display(self):
        from core.services.observations import observation_rows
        rows=[self.row(stock=0),self.row(stock=1,stock_quantity_exact=False),self.row(stock_known=False)]
        text=[r['재고 응답'] for r in observation_rows(rows,37.5,127,2)]
        self.assertIn('0개',text[0])
        self.assertIn('수량 미확인',text[1])
        self.assertEqual(text[2],'미확인')

    def test_explicit_event_enrichment_does_not_refetch_or_restamp_stock(self):
        from core.cart_models import QuerySnapshot
        from core.services.query_service import add_promotion_observations
        row=self.row(price=0)
        class Events:
            calls=[]
            def search(self,brand,name,*,allow_catalog_fetch=False):
                self.calls.append((brand,name,allow_catalog_fetch))
                return [replace(row,store_name='',address='',store_code='',price=2500,
                                price_data_status='REFERENCE',price_source='공식 목록',stock_known=False)]
            def status_for(self,*args):
                return {'status':'PARTIAL','message':'일부 목록만 확인'}
        events=Events()
        manager=SimpleNamespace(promotion_manager=events,search_selected=Mock())
        snapshot=QuerySnapshot([row,replace(row,store_code='S2')],[],{},(),'원래시작','원래완료')
        updated,notes=add_promotion_observations(snapshot,manager)
        self.assertEqual(len(events.calls),1)
        self.assertTrue(events.calls[0][2])
        manager.search_selected.assert_not_called()
        self.assertEqual(updated.records[0].stock,row.stock)
        self.assertEqual(updated.records[0].received_at,row.received_at)
        self.assertEqual(updated.records[0].source_updated_at,'')
        self.assertEqual(updated.records[0].price,2500)
        self.assertEqual(row.price,0)
        self.assertEqual(updated.completed_at,'원래완료')
        self.assertTrue(notes)

    def test_only_failed_brand_is_retried_and_success_timestamps_are_preserved(self):
        from core.cart_models import QuerySnapshot
        from core.services.query_service import retry_failed_brands
        cu=self.row()
        seven=replace(cu,brand='세븐일레븐',stock=5)
        statuses=[DataLoadResult('CU',[cu],'LIVE','정상'),DataLoadResult('세븐일레븐',[],'ERROR','502')]
        snapshot=QuerySnapshot([cu],statuses,{'i':statuses},(),'처음','원래완료')
        manager=Mock()
        manager.search_all.return_value=([seven],[DataLoadResult('세븐일레븐',[seven],'LIVE','정상')])
        cart=[CartItem('i','펩시600ml',1)]
        links=[CatalogLink('i',brand,'','P','펩시600ml') for brand in ('CU','세븐일레븐')]
        updated,selected=retry_failed_brands(snapshot,cart,links,manager,37.5,127,['CU','세븐일레븐'],'LIVE')
        self.assertEqual(selected,['세븐일레븐'])
        manager.search_all.assert_called_once_with('펩시600ml',37.5,127,['세븐일레븐'],'LIVE',include_unpriced=True,force_refresh=True)
        kept=next(row for row in updated.records if row.brand=='CU')
        self.assertEqual(kept.received_at,cu.received_at)
        self.assertTrue(kept.app_cache_used)
        self.assertFalse(cu.app_cache_used)
        self.assertEqual({s.brand:s.source for s in updated.statuses},{'CU':'LIVE','세븐일레븐':'LIVE'})

    def test_unlinked_brand_alone_does_not_trigger_remote_retry(self):
        from core.cart_models import QuerySnapshot
        from core.services.query_service import retry_failed_brands
        status=DataLoadResult('CU',[],'UNLINKED','상품 연결 필요')
        snapshot=QuerySnapshot([], [status],{'i':[status]},(),'처음','완료')
        manager=Mock()
        result,selected=retry_failed_brands(snapshot,[CartItem('i','음료',1)],[],manager,37,127,['CU'],'LIVE')
        self.assertIs(result,snapshot)
        self.assertEqual(selected,[])
        manager.search_all.assert_not_called()
