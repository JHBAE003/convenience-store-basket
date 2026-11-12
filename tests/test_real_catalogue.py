"""공개 사실과 TEST 가정의 분리·규격 보존·재현성을 외부 통신 없이 확인한다."""
import json
from pathlib import Path
import tempfile
import unittest

from planner.engine import Offer,promotion_state
from planner.test_data import TestDataset
from scripts.build_real_test_dataset import ROOT,build,identity,spec_fields


class RealCatalogueTests(unittest.TestCase):
    def test_every_identity_has_exact_observed_name_and_facts(self):
        catalogue=json.loads((ROOT/'data/real_catalogue.json').read_text(encoding='utf-8'))
        raw=[r for name in ('research_cu_emart.json','research_gs_seven.json')
             for r in json.loads((ROOT/'data'/name).read_text(encoding='utf-8'))['products']]
        observations={(r['brand'],r['name'],r['source_url'],r.get('promotion'),r['reference_price']) for r in raw}
        self.assertEqual(len(catalogue['products']),300)
        self.assertEqual(len({identity(p['name']) for p in catalogue['products']}),300)
        for p in catalogue['products']:
            self.assertTrue(p['facts']);self.assertEqual(p['name'],p['facts'][0]['name'])
            for row in p['facts']:
                self.assertIn((row['brand'],row['name'],row['source_url'],row.get('promotion'),row['reference_price']),observations)

    def test_identity_does_not_remove_volume_taste_or_pack_differences(self):
        names=('제품딸기250ml','제품딸기500ml','제품초코250ml','제품딸기250ml*6입')
        self.assertEqual(len({identity(x) for x in names}),4)
        self.assertEqual(spec_fields('샘물500ml*20입')[1],20)
        self.assertEqual(spec_fields('음료190ml*10')[1],10)
        self.assertEqual(spec_fields('샘물500ml')[1],0)
        self.assertEqual(spec_fields('규격이없는공개상품')[0],'규격 미제공')

    def test_source_unknowns_stay_unknown_and_branch_values_stay_simulated(self):
        with TestDataset().connect() as db:
            rows=db.execute('SELECT * FROM offers').fetchall()
            self.assertEqual(len(rows),7200)
            self.assertTrue(all(r['synthetic']==1 and r['stock_basis']=='가상 재고' for r in rows))
            self.assertTrue(all('TEST' in r['price_basis'] and '가정' in r['promotion_basis'] for r in rows))
            # 이번 확보 자료는 상품별 행사 기간과 원본 갱신 시각을 제공하지 않았다.
            self.assertTrue(all(r['source_promotion_start'] is None and r['source_promotion_end'] is None and r['source_updated_at'] is None for r in rows))
            self.assertEqual(db.execute("SELECT count(*) FROM brand_products WHERE barcode<>''").fetchone()[0],0)
            self.assertGreater(db.execute("SELECT count(*) FROM brand_products WHERE listing_basis LIKE '%미확인%'").fetchone()[0],0)

    def test_explicit_card_condition_is_excluded_and_expired_period_stays_expired(self):
        catalogue=json.loads((ROOT/'data/real_catalogue.json').read_text(encoding='utf-8'))
        product=next(p for p in catalogue['products'] if any(r.get('promotion') in ('1+1','2+1') for r in p['facts']))
        brand=product['facts'][0]['brand']
        for row in product['facts']:
            if row['brand']==brand:row.update(membership_required=True,promotion='1+1')
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'catalogue.json';source.write_text(json.dumps(catalogue,ensure_ascii=False),encoding='utf-8')
            output=build(Path(folder)/'check.sqlite3',catalogue=source)
            with TestDataset(output).connect() as db:
                rows=db.execute('SELECT o.* FROM offers o JOIN stores s ON s.id=o.store_id WHERE product_id=? AND brand=?',(product['id'],brand)).fetchall()
                self.assertEqual(len(rows),6);self.assertTrue(all(r['promotion']=='' for r in rows))
        expired=Offer('s','p',2000,4,'1+1','2026-10-01','2026-10-31',True,'규격')
        self.assertEqual(promotion_state(expired,'2026-11-12')[0],'')

    def test_test_defaults_use_real_names_and_source_receipt_is_not_stock_time(self):
        from planner.providers import TestProvider,BRANDS,query_context
        provider=TestProvider();products,links,origin=provider.defaults()
        self.assertEqual(len(products),3)
        self.assertTrue(all('시연' not in p.name and '[가상]' not in p.name for p in products))
        snapshot=provider.lookup(products,links,origin,BRANDS,query_context('TEST',products,links,origin,BRANDS))
        self.assertTrue(all(r['앱 수신 시각']=='저장된 TEST 파일' for r in snapshot.observations))
        self.assertTrue(all('TEST 판매가·재고' in r['원본 자료 시각'] for r in snapshot.observations))
        self.assertTrue(any(r['참고 자료 확인 시각'] for r in snapshot.observations))
