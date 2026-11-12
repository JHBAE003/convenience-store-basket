"""정규화된 SQLite 시연 데이터의 읽기 전용 조회. 외부 통신이나 실행 중 생성 없음."""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager

DATA_PATH=Path(__file__).resolve().parents[1]/'data/test_dataset.sqlite3'
DEFAULT_PRODUCTS=('TEST-P003','TEST-P027','TEST-P077')


class TestDataset:
    def __init__(self,path=DATA_PATH):
        self.path=Path(path).resolve()
        if not self.path.is_file():raise FileNotFoundError('시연 데이터 파일(data/test_dataset.sqlite3)이 없습니다.')

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
        connection.row_factory=sqlite3.Row
        try:yield connection
        finally:connection.close()

    def metadata(self):
        with self.connect() as db:
            return {r['key']:json.loads(r['value']) for r in db.execute('SELECT key,value FROM metadata')}

    def stores(self):
        with self.connect() as db:
            return tuple(dict(r) for r in db.execute('SELECT * FROM stores ORDER BY brand,distance_m,id'))

    def counts(self):
        with self.connect() as db:
            return {table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('stores','products','offers')}

    def catalog(self):
        with self.connect() as db:return tuple(dict(r) for r in db.execute('SELECT * FROM products ORDER BY id'))

    def search(self,keyword,brands,product_ids=()):
        if not brands:return ()
        # LIKE 특수 문자도 일반 검색어로 처리하고 값은 모두 매개변수로 전달한다.
        value=keyword.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        query='''SELECT p.*,b.brand,b.product_code,b.barcode FROM products p
                 JOIN brand_products b ON b.product_id=p.id WHERE b.brand IN ('''+','.join('?' for _ in brands)+')'
        args=list(brands)
        if product_ids:
            query+=' AND p.id IN ('+','.join('?' for _ in product_ids)+')';args.extend(product_ids)
        query+=" AND (p.name LIKE ? ESCAPE '\\' OR p.spec LIKE ? ESCAPE '\\' OR p.category LIKE ? ESCAPE '\\') ORDER BY p.id,b.brand"
        args.extend(['%'+value+'%']*3)
        with self.connect() as db:return tuple(dict(r) for r in db.execute(query,args))

    def offers(self,product_ids,brands):
        if not product_ids or not brands:return ()
        query='''SELECT o.*,p.name,p.spec,s.brand FROM offers o JOIN products p ON p.id=o.product_id
                 JOIN stores s ON s.id=o.store_id WHERE o.product_id IN ('''+','.join('?' for _ in product_ids)+')'
        query+=' AND s.brand IN ('+','.join('?' for _ in brands)+') ORDER BY s.id,p.id'
        with self.connect() as db:return tuple(dict(r) for r in db.execute(query,[*product_ids,*brands]))
