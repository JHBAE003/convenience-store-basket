import csv
import io
import unittest
from core.cart_models import CartItem
from core.services.catalog import load_catalog, resolve_cart_records
from core.services.query_service import new_session_manager, fetch_snapshot
from core.services.basket import evaluate_stores
from core.services.exporting import comparison_csv


class ExportingTests(unittest.TestCase):
    def test_same_snapshot_korean_bom_and_missing_prices(self):
        manager,temp=new_session_manager()
        self.addCleanup(temp.cleanup)
        cart=[CartItem('drink','음료',2),CartItem('noodle','컵라면',1),CartItem('snack','과자',1)]
        links=load_catalog()
        snapshot=fetch_snapshot(cart,links,manager,37.458115,126.952161,['CU','GS25','세븐일레븐','이마트24'],'TEST')
        stores=evaluate_stores(cart,resolve_cart_records(cart,snapshot.records,links),37.458115,126.952161,2)
        data=comparison_csv(stores,snapshot)
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
        self.assertEqual(next(r for r in rows if r['매장ID']=='CU|A')['확정총액'],'5300')
        self.assertEqual(next(r for r in rows if r['매장ID']=='CU|E')['확정총액'],'')
        self.assertTrue(all(r['데이터상태']=='TEST' for r in rows))
        self.assertEqual(rows[0]['비교완료시각'],snapshot.completed_at)
