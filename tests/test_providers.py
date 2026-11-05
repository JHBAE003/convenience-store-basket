import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from planner.engine import Product, Settings, solve, MODES
from planner.providers import (BRANDS, LiveProvider, TestProvider, Snapshot, query_context,
                               candidate_from_row, link_conflict, load_legacy, calculation_scope)

load_legacy()
from core.models import StoreProduct, DataLoadResult


def row(**changes):
    values=dict(brand='CU',store_name='확인점',address='서울 중구',product_name='제조사)흰우유500ml',
                product_code='CU-MILK',price=1200,stock=8,latitude=37.5674,longitude=126.978,
                store_code='CU-STORE',data_source='LIVE',stock_known=True,stock_quantity_exact=True,
                price_data_status='LIVE',stock_data_status='LIVE',promotion_known=False,
                received_at='2026-11-05T11:00:00+09:00',source_updated_at='',
                manufacturer='제조사',volume='500ml',flavor='무가당',pack_count='1')
    values.update(changes)
    return StoreProduct(**values)


class FakeManager:
    cache_ttl_seconds=60

    def __init__(self, rows=None, source='LIVE', age=None):
        self.rows=[row()] if rows is None else rows
        self.source=source;self.age=age;self.calls=[]

    def search_products(self, keyword, lat, lng, brands, mode):
        self.calls.append(('search',keyword,lat,lng,tuple(brands),mode))
        rows=[replace(r,store_name='',address='',store_code='',stock_known=False,price_data_status='REFERENCE')
              for r in self.rows if r.brand in brands]
        return rows,[DataLoadResult(b,[],'LIVE','모의 응답') for b in brands]

    def search_selected(self, link, lat, lng, mode, **options):
        self.calls.append(('selected',link,lat,lng,mode,options))
        return self.rows,[DataLoadResult(link.brand,self.rows,self.source,'모의 재고 응답',self.age)]


class ProviderTests(unittest.TestCase):
    def inputs(self, manager):
        c=candidate_from_row(row(store_name='',address=''))
        products=(Product(c.id,c.name,c.spec),)
        links={c.id:[c]}
        context=query_context('LIVE',products,links,(37.5665,126.978),['CU'])
        return LiveProvider(manager),products,links,context

    def lookup(self, manager,force=False):
        provider,products,links,context=self.inputs(manager)
        snap=provider.lookup(products,links,(37.5665,126.978),['CU'],context,force)
        return snap,products

    def test_test_provider_uses_common_contract_without_live_initialization(self):
        with patch.object(LiveProvider,'manager',side_effect=AssertionError('TEST에서 LIVE 초기화')):
            provider=TestProvider()
            found=provider.search('',(37.5665,126.978),BRANDS)
            self.assertGreater(len(found.candidates),0)
            products,links,origin=provider.defaults()
            snap=provider.lookup(products,links,origin,BRANDS,('TEST',))
            self.assertIsInstance(snap,Snapshot)
            self.assertEqual(len(snap.stores),24)
            self.assertEqual(solve(products,snap.stores,snap.offers,{p.id:1 for p in products},Settings(origin=origin)).winners[MODES[0]].total,4100)

    def test_live_selected_code_and_force_are_forwarded(self):
        manager=FakeManager()
        snap,products=self.lookup(manager,True)
        self.assertEqual(manager.calls[0][0],'selected')
        self.assertEqual(manager.calls[0][1].product_code,'CU-MILK')
        self.assertEqual(manager.calls[0][-1],dict(include_unpriced=True,force_refresh=True))
        result=solve(products,snap.stores,snap.offers,{products[0].id:2},Settings())
        self.assertEqual(result.winners[MODES[0]].total,2400)

    def test_zero_stock_is_not_failure_or_fake(self):
        snap,products=self.lookup(FakeManager([row(stock=0)]))
        self.assertEqual(snap.offers[0].stock,0)
        self.assertEqual(snap.observations[0]['재고'],'0')
        self.assertIsNone(solve(products,snap.stores,snap.offers,{products[0].id:1},Settings()).winners[MODES[0]])

    def test_reference_price_unknown_and_inexact_stock_are_not_recommendations(self):
        for changes in (dict(price_data_status='REFERENCE'),dict(stock_known=False),dict(stock_quantity_exact=False),dict(stock=None)):
            with self.subTest(changes=changes):
                snap,products=self.lookup(FakeManager([row(**changes)]))
                result=solve(products,snap.stores,snap.offers,{products[0].id:1},Settings())
                self.assertIsNone(result.winners[MODES[0]])
                self.assertEqual(len(snap.observations),1)

    def test_cache_after_failure_is_only_reference_and_timestamps_preserved(self):
        snap,products=self.lookup(FakeManager([row(app_cache_used=True)],source='CACHE',age=70))
        self.assertIsNone(snap.offers[0].price)
        self.assertIsNone(snap.offers[0].stock)
        self.assertEqual(snap.observations[0]['앱 수신 시각'],'2026-11-05T11:00:00+09:00')
        self.assertEqual(snap.observations[0]['원본 자료 시각'],'미제공')
        self.assertEqual(snap.ttl,0)

    def test_failures_and_empty_results_do_not_substitute_test_data(self):
        manager=FakeManager([])
        snap,_=self.lookup(manager)
        self.assertEqual(snap.offers,())
        manager.search_selected=Mock(side_effect=RuntimeError('external failed'))
        snap,_=self.lookup(manager)
        self.assertEqual(snap.offers,())
        self.assertIn('실패',snap.messages[0])
        with patch.object(LiveProvider,'manager',side_effect=RuntimeError('init failed')):
            snap,_=self.lookup(FakeManager())
            self.assertEqual(snap.offers,())

    def test_foreign_product_rows_rejected(self):
        snap,_=self.lookup(FakeManager([row(product_code='OTHER')]))
        self.assertEqual(snap.offers,())
        self.assertIn('식별',snap.messages[-1])

    def test_conflicting_duplicate_rows_cannot_choose_lower_price(self):
        snap,_=self.lookup(FakeManager([row(),row(price=500)]))
        self.assertEqual(len(snap.offers),1)
        self.assertIsNone(snap.offers[0].price)

    def test_context_and_ttl_isolate_mode_product_position_brands_links(self):
        provider,products,links,context=self.inputs(FakeManager())
        snap=Snapshot(context=context,loaded_at=100,ttl=60)
        self.assertTrue(snap.usable(context,159))
        self.assertFalse(snap.usable(context,160))
        for mode,ps,ls,origin,brands in (
                ('TEST',products,links,(37.5665,126.978),['CU']),
                ('LIVE',(),{},(37.5665,126.978),['CU']),
                ('LIVE',products,links,(37.5,126.978),['CU']),
                ('LIVE',products,links,(37.5665,126.978),['GS25']),
                ('LIVE',products,{},(37.5665,126.978),['CU'])):
            self.assertFalse(snap.usable(query_context(mode,ps,ls,origin,brands),101))

    def test_age_from_manager_cache_reduces_remaining_ttl(self):
        snap,_=self.lookup(FakeManager(age=55))
        self.assertEqual(snap.ttl,5)
        self.assertFalse(snap.usable(snap.context,snap.loaded_at+5))

    def test_expired_live_promotion_uses_regular_price(self):
        snap,products=self.lookup(FakeManager([row(promotion='1+1',promotion_known=True,
            promotion_data_status='LIVE',promotion_conditions_verified=True,
            promotion_start_date='2026-10-01',promotion_end_date='2026-10-31')]))
        result=solve(products,snap.stores,snap.offers,{products[0].id:2},Settings(as_of='2026-11-01'))
        self.assertEqual(result.winners[MODES[0]].total,2400)

    def test_conflicting_variants_cannot_be_user_linked(self):
        first=candidate_from_row(row())
        for r in (row(volume='1L',product_name='제조사)흰우유1L'),row(flavor='딸기'),row(pack_count='2')):
            self.assertTrue(link_conflict(first,candidate_from_row(r)))

    def test_live_promotions_recheck_member_card_cross_gift_and_bundle_quantities(self):
        standard=dict(promotion='1+1',promotion_known=True,promotion_data_status='LIVE',
            promotion_conditions_verified=True,promotion_start_date='2026-11-01',promotion_end_date='2026-11-30',
            promotion_member_condition='없음',promotion_card_condition='없음',promotion_cross_gift=False,
            promotion_buy_quantity=1,promotion_gift_quantity=1)
        for changes in ({},{'promotion_member_condition':'회원 전용'},{'promotion_card_condition':'카드 필요'},
                        {'promotion_cross_gift':True},{'promotion_cross_gift':None},{'promotion_buy_quantity':2},
                        {'promotion_gift_quantity':None}):
            with self.subTest(changes=changes):
                snap,products=self.lookup(FakeManager([row(**(standard|changes))]))
                result=solve(products,snap.stores,snap.offers,{products[0].id:2},Settings(as_of='2026-11-15'))
                self.assertEqual(result.winners[MODES[0]].total,2400 if changes else 1200)

    def test_store_limit_applies_after_radius_filter_and_does_not_destroy_observations(self):
        records=[row()]+[row(store_code=f'FAR-{i}',store_name=f'원거리점{i}',latitude=35+i/100) for i in range(12)]
        snap,products=self.lookup(FakeManager(records))
        self.assertEqual(len(snap.stores),13)
        stores,offers,issue=calculation_scope(snap,Settings(radius_km=1))
        self.assertEqual(len(stores),1);self.assertFalse(issue)
        self.assertEqual(solve(products,stores,offers,{products[0].id:1},Settings()).winners[MODES[0]].total,1200)
        self.assertEqual(len(snap.observations),13)

    def test_store_limit_reports_issue_when_all_twenty_five_stores_in_radius(self):
        records=[row(store_code=f'NEAR-{i}',store_name=f'근거리점{i}') for i in range(25)]
        snap,_=self.lookup(FakeManager(records))
        stores,offers,issue=calculation_scope(snap,Settings())
        self.assertEqual((stores,offers),((),()))
        self.assertIn('25곳',issue)


if __name__=='__main__':unittest.main()
