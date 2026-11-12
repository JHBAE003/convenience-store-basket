"""확인된 점포 목록 + 재현 가능한 가상 상품 300종을 SQLite로 만든다. 외부 요청 없음."""
from pathlib import Path
import argparse
import hashlib
import json
import random
import sqlite3
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
SEED=310300
FIXED_DATE='2026-11-15'
GROUPS=(
    ('우유','시연목장',('흰 우유','딸기 우유','초코 우유','바나나 우유','커피 우유'),(200,250,500,900,1000),'ml',1500),
    ('과자','시연제과',('감자 과자','옥수수 스낵','쌀 과자','프레첼','고구마칩'),(40,60,80,100,150),'g',1300),
    ('음료','시연음료',('콜라','사이다','오렌지 주스','이온 음료','녹차'),(250,350,500,1000,1500),'ml',1300),
    ('생수','시연샘물',('생수','탄산수','레몬 탄산수','자몽 탄산수','라임 탄산수'),(330,500,750,1000,2000),'ml',800),
    ('컵라면','시연식품',('김치 컵라면','해물 컵라면','우동 컵라면','짬뽕 컵라면','순한 컵라면'),(65,80,100,110,120),'g',1200),
    ('즉석밥','시연식품',('백미밥','현미밥','흑미밥','잡곡밥','콩밥'),(130,180,200,210,300),'g',1500),
    ('샌드위치','시연키친',('햄치즈 샌드위치','에그 샌드위치','참치 샌드위치','닭가슴살 샌드위치','감자 샌드위치'),(120,150,180,210,240),'g',2800),
    ('도시락','시연키친',('제육 도시락','불고기 도시락','치킨 도시락','돈까스 도시락','두부 도시락'),(300,350,400,450,500),'g',3900),
    ('아이스크림','시연빙과',('바닐라 아이스크림','초코 아이스크림','딸기 아이스크림','녹차 아이스크림','쿠키 아이스크림'),(90,120,150,300,500),'ml',1300),
    ('커피','시연카페',('아메리카노','카페라떼','바닐라라떼','모카라떼','디카페인 커피'),(200,250,300,400,500),'ml',1700),
    ('초콜릿','시연제과',('밀크 초콜릿','다크 초콜릿','화이트 초콜릿','아몬드 초콜릿','크런치 초콜릿'),(20,30,40,60,100),'g',1000),
    ('생활용품','시연생활',('물티슈','휴대용 휴지','위생장갑','지퍼백','면봉'),(5,10,20,30,50),'개입',900),
)


def catalog():
    products=[]
    for category,maker,names,sizes,unit,base in GROUPS:
        for flavor_index,name in enumerate(names):
            for size_index,size in enumerate(sizes):
                pid=f'TEST-P{len(products)+1:03d}'
                volume=f'{size}{unit}'
                spec=f'{volume} · {name} · '+('1팩' if unit=='개입' else '1개')
                products.append((pid,f'[가상] {maker} {name} {volume}',spec,category,maker,volume,name,
                                 size if unit=='개입' else 1,base+size_index*300+flavor_index*100))
    assert len(products)==300
    return products


def build(output,source=ROOT/'data/store_sources.json'):
    output=Path(output)
    if output.exists():raise FileExistsError('기존 데이터 파일을 덮어쓰지 않습니다. 다른 출력 경로를 지정하세요.')
    facts=json.loads(Path(source).read_text(encoding='utf-8'))
    stores=facts['stores']
    brands=('CU','GS25','세븐일레븐','이마트24')
    if len(stores)!=24 or any(sum(s['brand']==b for s in stores)!=6 for b in brands):
        raise ValueError('브랜드별 6곳, 전체 24곳을 먼저 확인해야 합니다.')
    output.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(output)) as db:
        db.executescript('''
        PRAGMA foreign_keys=ON;
        PRAGMA user_version=1;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE stores(id TEXT PRIMARY KEY,brand TEXT NOT NULL,name TEXT NOT NULL,address TEXT NOT NULL,
          lat REAL NOT NULL CHECK(lat BETWEEN 37 AND 38),lng REAL NOT NULL CHECK(lng BETWEEN 126 AND 128),
          source_url TEXT NOT NULL,source_kind TEXT NOT NULL,source_detail TEXT NOT NULL,checked_at TEXT NOT NULL,
          source_updated_at TEXT NOT NULL,location_basis TEXT NOT NULL,operating_status TEXT NOT NULL,distance_m INTEGER NOT NULL);
        CREATE INDEX stores_brand ON stores(brand);
        CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT NOT NULL,spec TEXT NOT NULL,category TEXT NOT NULL,
          manufacturer TEXT NOT NULL,volume TEXT NOT NULL,flavor TEXT NOT NULL,pack_count INTEGER NOT NULL,
          base_price INTEGER NOT NULL,synthetic INTEGER NOT NULL DEFAULT 1 CHECK(synthetic=1));
        CREATE TABLE brand_products(brand TEXT NOT NULL,product_id TEXT NOT NULL REFERENCES products(id),
          product_code TEXT NOT NULL UNIQUE,barcode TEXT NOT NULL DEFAULT '',PRIMARY KEY(brand,product_id)) WITHOUT ROWID;
        CREATE TABLE offers(store_id TEXT NOT NULL REFERENCES stores(id),product_id TEXT NOT NULL REFERENCES products(id),
          price INTEGER NOT NULL CHECK(price>0),stock INTEGER NOT NULL CHECK(stock>=0),
          promotion TEXT NOT NULL CHECK(promotion IN ('','1+1','2+1')),
          start TEXT NOT NULL,end TEXT NOT NULL,conditions_verified INTEGER NOT NULL CHECK(conditions_verified=1),
          synthetic INTEGER NOT NULL CHECK(synthetic=1),PRIMARY KEY(store_id,product_id)) WITHOUT ROWID;
        CREATE INDEX offers_product ON offers(product_id,store_id);
        ''')
        metadata={'schema_version':1,'generator_seed':SEED,'fixed_date':FIXED_DATE,
                  'real_fields':'점포명·주소·좌표·출처','synthetic_fields':'상품·식별자·가격·행사·재고 전체',
                  'origin':facts['origin'],'researched_at':facts['researched_at']}
        db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in metadata.items()])
        keys=('id','brand','name','address','lat','lng','source_url','source_kind','source_detail','checked_at',
              'source_updated_at','location_basis','operating_status','distance_m')
        db.executemany('INSERT INTO stores VALUES ('+','.join('?' for _ in keys)+')',[tuple(s[k] for k in keys) for s in stores])
        products=catalog()
        db.executemany('INSERT INTO products(id,name,spec,category,manufacturer,volume,flavor,pack_count,base_price) VALUES (?,?,?,?,?,?,?,?,?)',products)
        for b_index,brand in enumerate(brands):
            db.executemany('INSERT INTO brand_products VALUES (?,?,?,?)',[(brand,p[0],f'TEST-B{b_index+1}-{p[0]}','') for p in products])
        offers=[]
        for store in stores:
            for product in products:
                # 안정적인 해시 난수 시드로 실행 시각과 Python hash값에 의존하지 않는다.
                seed=int.from_bytes(hashlib.sha256(f'{SEED}|{store["id"]}|{product[0]}'.encode()).digest()[:8])
                rng=random.Random(seed)
                price=max(500,product[-1]+rng.choice((-300,-200,-100,0,100,200,300)))
                stock=0 if rng.randrange(10)==0 else rng.randint(1,40)
                promotion=rng.choices(('','1+1','2+1'),(6,2,2))[0]
                offers.append((store['id'],product[0],price,stock,promotion,'2026-11-01','2026-11-30',1,1))
        db.executemany('INSERT INTO offers VALUES (?,?,?,?,?,?,?,?,?)',offers)
        db.commit()
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute('SELECT count(*) FROM offers').fetchone()[0]==7200
        db.execute('VACUUM')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'data/test_dataset.sqlite3')
    args=parser.parse_args();path=build(args.output)
    print(f'{path}: 24개 실제 점포 위치 / 300종 가상 상품 / 7,200개 가상 판매 자료 / {path.stat().st_size:,} bytes')
