from pathlib import Path
import sqlite3
import tempfile
import unittest

from planner.test_data import TestDataset,DEFAULT_PRODUCTS
from planner.providers import TestProvider,BRANDS,query_context,calculation_scope
from planner.engine import Settings,solve,MODES,haversine_km
from scripts.build_test_dataset import build


class DatasetTests(unittest.TestCase):
    def setUp(self):self.data=TestDataset()

    def test_exact_counts_and_actual_location_provenance(self):
        self.assertEqual(self.data.counts(),dict(stores=24,products=300,offers=7200))
        stores=self.data.stores()
        self.assertEqual(len({s['id'] for s in stores}),24)
        origin=self.data.metadata()['origin']
        for b in BRANDS:self.assertEqual(sum(s['brand']==b for s in stores),6)
        for s in stores:
            self.assertTrue(s['source_url'].startswith('https://'))
            self.assertTrue(s['checked_at']);self.assertIn('관악',s['address'])
            self.assertLess(haversine_km(origin['lat'],origin['lng'],s['lat'],s['lng']),2)
            self.assertNotIn('시연',s['name'])
        with self.data.connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
            counts=db.execute('SELECT count(*) FROM offers GROUP BY store_id').fetchall()
            self.assertEqual([r[0] for r in counts],[300]*24)
            self.assertEqual(db.execute('SELECT count(DISTINCT product_code) FROM brand_products').fetchone()[0],1200)
            self.assertEqual(db.execute("SELECT count(*) FROM brand_products WHERE barcode<>''").fetchone()[0],0)

    def test_database_is_read_only_and_missing_file_has_no_fallback(self):
        with self.data.connect() as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM offers')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):TestProvider(Path(folder)/'missing.sqlite3')

    def test_search_brand_size_category_and_literal_sql_characters(self):
        result=self.data.search('흰 우유 500ml',['CU'])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['id'],'TEST-P003')
        self.assertEqual(len(self.data.search('우유',BRANDS)),100)
        self.assertEqual(len(self.data.search('생활용품',['GS25'])),25)
        self.assertFalse(self.data.search("%' OR 1=1 --",BRANDS))
        self.assertFalse(self.data.search('%',BRANDS))
        self.assertFalse(self.data.search('우유',[]))

    def test_seed_rebuild_reproduces_all_virtual_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path=build(Path(folder)/'test.sqlite3')
            other=TestDataset(path)
            with self.data.connect() as original,other.connect() as regenerated:
                for table in ('products','brand_products','offers'):
                    self.assertEqual([tuple(r) for r in original.execute('SELECT * FROM '+table)],
                                     [tuple(r) for r in regenerated.execute('SELECT * FROM '+table)])
            with self.assertRaises(FileExistsError):build(path)

    def test_all_24_stores_participate_and_default_cost_is_independently_bounded(self):
        provider=TestProvider();products,links,origin=provider.defaults()
        context=query_context('TEST',products,links,origin,BRANDS)
        snapshot=provider.lookup(products,links,origin,BRANDS,context)
        settings=Settings(origin=origin,radius_km=2)
        stores,offers,issue=calculation_scope(snapshot,settings)
        self.assertFalse(issue);self.assertEqual(len(stores),24)
        result=solve(products,stores,offers,{p.id:1 for p in products},settings)
        # 품목 3종·각 1개이면 최대 3곳에서 품목별 최저 판매가를 모두 확보할 수 있다.
        with self.data.connect() as db:
            lower=sum(db.execute('SELECT min(price) FROM offers WHERE product_id=? AND stock>=1',(pid,)).fetchone()[0] for pid in DEFAULT_PRODUCTS)
        self.assertEqual(result.winners[MODES[0]].total,lower)
        self.assertEqual(result.combinations_checked,24+276+2024)


if __name__=='__main__':unittest.main()
