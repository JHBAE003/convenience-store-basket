from pathlib import Path
from dataclasses import replace
import unittest
from streamlit.testing.v1 import AppTest
from planner.providers import TestProvider,BRANDS,group_search_candidates
from planner.test_data import TestDataset
from planner.engine import Product,Store,Offer,Settings
from planner.brand_summary import brand_offer_rows,observation_rows

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class SearchDataTests(unittest.TestCase):
    def test_test_search_keeps_every_brand_and_all_300_products(self):
        result=TestProvider().search('',(37.48121,126.9527),BRANDS)
        self.assertEqual(len(result.candidates),1200)
        self.assertEqual(len(group_search_candidates(result.candidates)),300)
        for brand in BRANDS:
            candidates=[c for c in result.candidates if c.brand==brand]
            self.assertEqual(len(candidates),300)
            self.assertEqual({c.seed_id for c in candidates},{f'TEST-P{i:03}' for i in range(1,301)})

    def test_different_sizes_and_flavors_remain_separate(self):
        result=TestProvider().search('우유',(37.48121,126.9527),BRANDS)
        groups=group_search_candidates(result.candidates)
        expected={p['id'] for p in TestDataset().catalog() if '우유' in p['name']+' '+p['spec']+' '+p['category']}
        self.assertEqual(len(groups),len(expected))
        self.assertGreater(len(groups),1)
        self.assertEqual({c.seed_id for group in groups for c in group},expected)
        self.assertGreater(len({group[0].spec for group in groups}),1)
        self.assertTrue(all(len({c.spec for c in group})==1 for group in groups))
        self.assertTrue(all(len(group)==4 for group in groups))


class BrandSummaryTests(unittest.TestCase):
    def test_prices_stock_and_promotions_are_not_one_brandwide_offer(self):
        p=Product('p','가상 우유','500ml')
        stores=tuple(Store(str(i),'CU','점포'+str(i),37.48,126.95) for i in range(1,5))
        offers=(Offer('1','p',1000,0,spec=p.spec),Offer('2','p',1200,3,'1+1',spec=p.spec),
                Offer('3','p',None,None,spec=p.spec),Offer('4','p',800,5,'2+1',end='2026-10-31',spec=p.spec))
        rows=brand_offer_rows(stores,offers,(p,),{'p':2},Settings())
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertNotIn('매장',row)
        self.assertEqual(row['확인 가격 범위'],'800~1,200원')
        self.assertEqual((row['재고 있음 (곳)'],row['재고 0 (곳)'],row['수량 미확인 (곳)']),(2,1,1))
        self.assertEqual(row['요청 수량 구매 가능 (곳)'],2)
        self.assertEqual(row['요청 수량 결제액 범위'],'1,200~1,600원')
        self.assertEqual(row['계산 가능한 행사'],'1+1')

    def test_receipt_times_keep_unknown_and_mixed_times_honest(self):
        observations=({'브랜드':'CU','점포 코드':'a','매장':'a','상품':'우유','앱 수신 시각':'2026-11-05T09:00:00+09:00','원본 자료 시각':None,'앱 캐시':'사용'},
                      {'브랜드':'CU','점포 코드':'b','매장':'b','상품':'우유','앱 수신 시각':'2026-11-05T09:01:00+09:00','원본 자료 시각':'미제공','앱 캐시':'사용 안 함'})
        row=observation_rows(observations,())[0]
        self.assertEqual(row['확인 점포 수'],2);self.assertEqual(row['원본 자료 시각'],'미제공')
        self.assertEqual(row['앱 수신 시각'],'여러 시각 (2개)')
        self.assertEqual(row['앱 캐시'],'사용 / 사용 안 함');self.assertNotIn('매장',row)
        mixed=observation_rows((observations[0],{**observations[1],'원본 자료 시각':'2026-11-05T08:00:00+09:00'}),())[0]
        self.assertEqual(mixed['원본 자료 시각'],'2026-11-05T08:00:00+09:00 · 일부 미제공')


class SearchUiTests(unittest.TestCase):
    def app(self):return AppTest.from_file(str(APP),default_timeout=30).run()

    def search(self,at,keyword):
        at.text_input(key='search_keyword').set_value(keyword)
        return at.button(key='search_button').click().run()

    def test_search_shows_all_related_products_in_brand_tabs_without_add_method(self):
        at=self.search(self.app(),'우유')
        self.assertFalse(at.exception)
        expected=len(TestDataset().search('우유',['CU']))
        self.assertGreater(expected,1)
        self.assertEqual([t.label for t in at.tabs],[f'{b} · {expected}종' for b in BRANDS])
        buttons=[b for b in at.button if (b.key or '').startswith('search_add_')]
        self.assertEqual(len(buttons),expected*len(BRANDS))
        self.assertEqual(len({b.key for b in buttons}),len(buttons))
        self.assertFalse(any(s.label in ('장바구니에 담는 방법','검색된 상품') for s in at.selectbox))
        self.assertFalse(any(c.key=='confirm_link' for c in at.checkbox))
        pid=next(r['id'] for r in TestDataset().search('우유',['CU']) if r['id'] not in {p.id for p in at.session_state['products']})
        at.session_state['manual_link_target']=at.session_state['products'][0].id
        at.session_state['confirm_link']=True
        at.button(key='search_add_T:CU:'+pid).click().run()
        self.assertEqual(len(at.session_state['links'][pid]),4)
        self.assertIsNone(at.session_state['snapshot'])
        self.assertTrue(at.button(key='search_add_T:GS25:'+pid).disabled)
        at.button(key='lookup_button').click().run()
        self.assertGreater(at.session_state['last_plan_count'],0);self.assertFalse(at.exception)

    def test_receipt_starts_collapsed_and_tables_are_brand_summaries(self):
        at=self.app()
        at.button_group(key='workspace_view').set_value('추천·경로').run()
        sources=next(e for e in at.expander if e.label=='브랜드별 TEST 데이터와 출처')
        source_table=next(e for e in sources if e.type=='dataframe').value
        self.assertEqual(len(source_table),4);self.assertTrue(all(source_table['포함 점포 수']==6))
        self.assertNotIn('위도',source_table.columns);self.assertNotIn('점포',source_table.columns)
        received=next(e for e in at.expander if e.label=='받은 자료와 확인 시각')
        self.assertFalse(received.proto.expanded)
        receipt_table=next(e for e in received if e.type=='dataframe').value
        self.assertEqual(len(receipt_table),12);self.assertNotIn('매장',receipt_table.columns)
        self.assertEqual(set(receipt_table['브랜드']),set(BRANDS))
        compared=next(e for e in at.expander if e.label=='상품별 가격·행사·재고 비교')
        compared_table=next(e for e in compared if e.type=='dataframe').value
        self.assertEqual(len(compared_table),12);self.assertNotIn('매장',compared_table.columns)
        self.assertTrue(all(compared_table['확인 점포 수']==6));self.assertFalse(at.exception)

    def test_brand_change_hides_previous_search_until_resubmitted(self):
        at=self.app()
        product=next(p for p in TestDataset().catalog() if p['id'] not in {p.id for p in at.session_state['products']})
        at=self.search(at,product['name'])
        at.multiselect(key='brands').set_value(['GS25']).run()
        self.assertFalse(any((b.key or '').startswith('search_add_') for b in at.button))
        at.button(key='search_button').click().run()
        expected=len(TestDataset().search(product['name'],['GS25']))
        self.assertEqual([t.label for t in at.tabs],[f'GS25 · {expected}종'])
        at.button(key='search_add_T:GS25:'+product['id']).click().run()
        self.assertEqual([c.brand for c in at.session_state['links'][product['id']]],['GS25'])
        self.assertFalse(at.exception)

    def test_broad_search_has_no_hidden_200_candidate_limit(self):
        keyword=')'
        rows=TestDataset().search(keyword,BRANDS);expected=len(rows)//len(BRANDS)
        self.assertGreater(len(rows),200)
        at=self.search(self.app(),keyword)
        self.assertEqual([t.label for t in at.tabs],[f'{b} · {expected}종' for b in BRANDS])
        self.assertEqual(sum((b.key or '').startswith('search_add_') for b in at.button),len(rows))
        last=rows[-1];self.assertTrue(at.button(key=f'search_add_T:{last["brand"]}:{last["id"]}'))
        self.assertFalse(at.exception)
