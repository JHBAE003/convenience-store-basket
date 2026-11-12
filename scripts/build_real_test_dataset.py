"""저장한 공개 상품·행사 사실과 명시한 TEST 가정으로 SQLite를 재현한다. 통신 없음."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
BRANDS=('CU','GS25','세븐일레븐','이마트24')
SEED=322300
FIXED_DATE='2026-11-12'
CATALOGUE=ROOT/'data/real_catalogue.json'


def identity(name):
    # 공백과 표시용 판매사 구분 기호만 정리한다. 용량·맛·묶음 수는 지우지 않는다.
    name=unicodedata.normalize('NFKC',name).strip()
    return re.sub(r'\s+','',name).casefold()


def category(name):
    if any(word in name for word in ('큰사발','사발면','사발컵','왕뚜껑','볶이컵','라면컵','신라면','짜파게티컵')):return '컵라면'
    rules=(('생활용품',('물티슈','휴지','칫솔','치약','샴푸','린스','세제','면봉','생리대','비누','마스크','건전지','콘돔','면도','가그린','리스테린','페브리즈','유연제','핫팩')),
           ('아이스크림',('아이스크림','메로나','월드콘','붕어싸만코','바밤바','빵빠레','스크류바','누가바','구구','하겐다즈','나뚜루','설레임','비비빅','더위사냥','빙수','크림바','모나카','파르페','파인트','왕실쿠키샌드','빵또아','젤라또','쿠앤크','슈퍼콘','끌레도르')),
           ('커피',('커피','카페','라떼','에스프레소','아메리카노','칸타타','맥심','스타벅스','조지아','레쓰비','바리스타','콜드브루')),
           ('우유',('우유','요구르트','요거','요플레','불가리스','비피더스','두유','베지밀','초코타임','딸기타임','비요뜨','액티비아','프로바이오틱','초코에몽','그릭','바이오','닥터캡슐','드링킹','어메이징오트','마이픽')),
           ('생수',('생수','삼다수','백산수','아이시스','탄산수','트레비','씨그램','평창수','에비앙','샘물')),
           ('즉석밥',('햇반','즉석밥','컵밥','죽','비빔밥','볶음밥')),
           ('식품·기타',('후랑크','소시지','소세지','만두','두부','치즈','불닭소스','육개장500','고기듬뿍')),
           ('컵라면',('라면','짜파','너구리','진짬뽕','짬뽕','불닭볶음면','신라면','사발면','사발컵','왕뚜껑','김치면','진라면','새우탕','튀김우동','오징어짬뽕')),
           ('초콜릿',('초콜릿','초콜렛','초코바','가나','크런키','스니커즈','트윅스','킷캣','허쉬','페레로')),
           ('과자',('과자','칩','스낵','칸쵸','꼬깔콘','새우깡','포카칩','포테토','초코송이','초코파이','홈런볼','빼빼로','비스킷','웨하스','오레오','쿠키','맛동산','몽쉘','프링글스','프레첼','크래커','젤리','사탕','껌')),
           ('샌드위치',('샌드위치','햄버거','핫도그','버거')),
           ('도시락',('도시락','정식','김밥','주먹밥')),
           ('음료',('주스','쥬스','콜라','사이다','차','음료','포카리','게토레이','파워에이드','환타','밀키스','웰치','몬스터','레드불','에너지','비타','이온','오로나민','박카스','핫식스','피크닉','알로에','토레타','코코팜')))
    for label,words in rules:
        if any(word in name for word in words):return label
    return '식품·기타'


def spec_fields(name):
    found=re.findall(r'\d+(?:\.\d+)?\s*(?:kg|ml|mL|ML|g|G|L|l|㎖|㎏|ℓ)(?![a-zA-Z])',name)
    volume=' / '.join(found) if found else '규격 미제공'
    pack=re.search(r'(\d+)\s*(?:개입|입|매입|매|캔입|팩입|봉입)',name)
    if not pack:pack=re.search(r'[*×xX]\s*(\d+)(?!\d)',name)
    pack_count=int(pack.group(1)) if pack else 0
    pack_text=f'{pack_count}개입 표기' if pack else '포장 수 미확인'
    return volume,pack_count,f'{volume} · {name} · {pack_text}'


def prepare_catalogue(paths,output=CATALOGUE):
    grouped=defaultdict(list);sources=[]
    for path in paths:
        payload=json.loads(Path(path).read_text(encoding='utf-8'))
        sources.extend(payload.get('sources',[]))
        for raw in payload['products']:
            name=raw['name'].strip()
            if raw.get('brand') not in BRANDS or not name or not raw.get('source_url'):continue
            # 19세 제한 상품은 수업용 기본 상품 목록에서 제외한다.
            if any(t in name for t in ('소주','맥주','와인','위스키','보드카','담배','막걸리','하이볼','청주','데킬라')):continue
            row=dict(raw)
            row['source_page_url']=('https://cu.bgfretail.com/event/plus.do?category=event&depth2=1&sf=N'
                                    if raw['brand']=='CU' else raw['source_url'])
            grouped[identity(name)].append(row)
    buckets=defaultdict(list)
    for key,rows in grouped.items():
        buckets[category(rows[0]['name'])].append((key,rows))
    for values in buckets.values():
        values.sort(key=lambda v:(spec_fields(v[1][0]['name'])[0]=='규격 미제공',
                                  -len({r['brand'] for r in v[1]}),hashlib.sha256(v[0].encode()).hexdigest()))
    chosen=[]
    # 실제로 관측된 항목만 고른다. 범주가 부족해도 새 용량·맛 조합을 만들지 않는다.
    while len(chosen)<300 and any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key] and len(chosen)<300:chosen.append(buckets[key].pop(0))
    if len(chosen)!=300:raise ValueError(f'검증 가능한 서로 다른 상품이 {len(chosen)}개뿐입니다. 임의 상품으로 채우지 않습니다.')
    products=[]
    for index,(key,rows) in enumerate(chosen,1):
        first=rows[0];name=first['name'];volume,pack,spec=spec_fields(name)
        prices=[r.get('reference_price') for r in rows if type(r.get('reference_price')) is int and r['reference_price']>0]
        if not prices:raise ValueError('공개 참고가가 없는 상품: '+name)
        products.append({'id':f'TEST-P{index:03d}','name':name,'spec':spec,'category':category(name),
                         'manufacturer':'미확인','volume':volume,'flavor':name,'pack_count':pack,
                         'base_price':min(prices),'identity_key':key,'facts':rows})
    # 기본 장바구니는 서로 다른 분류의 실제 상품 3종이다. ID는 버전 안에서 고정된다.
    defaults=[]
    for label in ('우유','과자','생수'):
        options=[p for p in products if p['category']==label and p['id'] not in defaults and p['pack_count']<=1]
        familiar={'우유':('초코에몽','맛있는우유','딸기우유','바나나','서울우유'),
                  '과자':('감자칩','리츠크래커','포카칩','새우깡','오레오쿠키','꼬깔콘','칸쵸'),
                  '생수':('500ml','500ML','동원샘물','삼다수','아이시스')}
        item=next((p for token in familiar[label] for p in options if token in p['name']),options[0] if options else None)
        if item:defaults.append(item['id'])
    defaults += [p['id'] for p in products if p['id'] not in defaults]
    payload={'version':'3.2.2','fixed_date':FIXED_DATE,'default_products':defaults[:3],
             'selection':'공개 목록에서 관측한 서로 다른 300개 상품. 규격/맛을 조합해 새 상품을 생성하지 않음.',
             'sources':sources,'products':products}
    Path(output).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return payload


def eligible_fact(rows,brand):
    matches=[r for r in rows if r['brand']==brand]
    return next((r for r in matches if r.get('source_kind')=='official' or r.get('source_type','').startswith('official')),matches[0] if matches else None)


def build(output,source=ROOT/'data/store_sources.json',catalogue=CATALOGUE):
    output=Path(output)
    if output.exists():raise FileExistsError('기존 데이터를 덮어쓰지 않습니다. 다른 출력 경로를 지정하세요.')
    real=json.loads(Path(catalogue).read_text(encoding='utf-8'))
    stores_data=json.loads(Path(source).read_text(encoding='utf-8'));stores=stores_data['stores']
    products=real['products']
    if len(products)!=300 or len(stores)!=24 or any(sum(s['brand']==b for s in stores)!=6 for b in BRANDS):raise ValueError('자료 개수 오류')
    output.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(output)) as db:
        db.executescript('''PRAGMA foreign_keys=ON; PRAGMA user_version=2;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE stores(id TEXT PRIMARY KEY,brand TEXT NOT NULL,name TEXT NOT NULL,address TEXT NOT NULL,lat REAL NOT NULL,lng REAL NOT NULL,
          source_url TEXT NOT NULL,source_kind TEXT NOT NULL,source_detail TEXT NOT NULL,checked_at TEXT NOT NULL,source_updated_at TEXT NOT NULL,
          location_basis TEXT NOT NULL,operating_status TEXT NOT NULL,distance_m INTEGER NOT NULL);
        CREATE INDEX stores_brand ON stores(brand);
        CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT NOT NULL,spec TEXT NOT NULL,category TEXT NOT NULL,manufacturer TEXT NOT NULL,
          volume TEXT NOT NULL,flavor TEXT NOT NULL,pack_count INTEGER NOT NULL,base_price INTEGER NOT NULL CHECK(base_price>0),
          synthetic INTEGER NOT NULL DEFAULT 1 CHECK(synthetic=1),identity_basis TEXT NOT NULL,source_url TEXT NOT NULL,source_checked_at TEXT NOT NULL);
        CREATE TABLE brand_products(brand TEXT NOT NULL,product_id TEXT NOT NULL REFERENCES products(id),product_code TEXT NOT NULL UNIQUE,
          barcode TEXT NOT NULL DEFAULT '',listing_basis TEXT NOT NULL,PRIMARY KEY(brand,product_id)) WITHOUT ROWID;
        CREATE TABLE product_sources(product_id TEXT NOT NULL REFERENCES products(id),brand TEXT NOT NULL,source_url TEXT NOT NULL,fact_json TEXT NOT NULL);
        CREATE INDEX sources_product ON product_sources(product_id,brand);
        CREATE TABLE offers(store_id TEXT NOT NULL REFERENCES stores(id),product_id TEXT NOT NULL REFERENCES products(id),
          price INTEGER NOT NULL CHECK(price>0),stock INTEGER NOT NULL CHECK(stock>=0),promotion TEXT NOT NULL CHECK(promotion IN ('','1+1','2+1')),
          start TEXT NOT NULL,end TEXT NOT NULL,conditions_verified INTEGER NOT NULL CHECK(conditions_verified IN(0,1)),synthetic INTEGER NOT NULL CHECK(synthetic=1),
          price_basis TEXT NOT NULL,stock_basis TEXT NOT NULL,promotion_basis TEXT NOT NULL,promotion_source_url TEXT NOT NULL,
          source_promotion_start TEXT,source_promotion_end TEXT,source_updated_at TEXT,source_checked_at TEXT NOT NULL,
          PRIMARY KEY(store_id,product_id)) WITHOUT ROWID;
        CREATE INDEX offers_product ON offers(product_id,store_id);
        ''')
        metadata={'schema_version':2,'generator_seed':SEED,'fixed_date':real['fixed_date'],
          'default_products':real['default_products'],'origin':stores_data['origin'],'researched_at':FIXED_DATE,
          'real_fields':'공개 상품명·표기 규격·출처별 참고가·공개 행사 유형 / 점포명·주소·좌표·출처',
          'synthetic_fields':'점포별 판매 여부·판매가·재고·미확인 행사 기간/조건. 다른 브랜드 판매 여부 미확인은 TEST 가정.',
          'promotion_assumption':'공개 행사 유형을 같은 상품 증정으로 가정. 미제공 실제 기간/조건은 원본에서 미확인으로 유지하고 TEST 기간/조건과 별도 저장.',
          'source_counts':dict(Counter(r['brand'] for p in products for r in p['facts'])),
          'catalogue_sha256':hashlib.sha256(Path(catalogue).read_bytes()).hexdigest()}
        db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v,ensure_ascii=False)) for k,v in metadata.items()])
        keys=('id','brand','name','address','lat','lng','source_url','source_kind','source_detail','checked_at','source_updated_at','location_basis','operating_status','distance_m')
        db.executemany('INSERT INTO stores VALUES ('+','.join('?' for _ in keys)+')',[tuple(s[k] for k in keys) for s in stores])
        for p in products:
            fact=p['facts'][0]
            values=[p[k] for k in ('id','name','spec','category','manufacturer','volume','flavor','pack_count','base_price')]
            values.extend((1,'공개 상품 목록',fact.get('source_page_url',fact['source_url']),fact.get('retrieved_at') or ''))
            db.execute('INSERT INTO products VALUES ('+','.join('?' for _ in values)+')',values)
            for row in p['facts']:db.execute('INSERT INTO product_sources VALUES (?,?,?,?)',(p['id'],row['brand'],row['source_url'],json.dumps(row,ensure_ascii=False)))
            for index,brand in enumerate(BRANDS,1):
                listed=eligible_fact(p['facts'],brand)
                db.execute('INSERT INTO brand_products VALUES (?,?,?,?,?)',(brand,p['id'],f'TEST-B{index}-{p["id"]}','','공개 목록 관측·점포 판매는 가정' if listed else '브랜드 판매 미확인·TEST 가정'))
        offers=[]
        for store in stores:
            for p in products:
                fact=eligible_fact(p['facts'],store['brand'])
                seed=int.from_bytes(hashlib.sha256(f'{SEED}|{store["id"]}|{p["id"]}'.encode()).digest()[:8]);rng=random.Random(seed)
                ref=fact.get('reference_price') if fact else None
                base=ref if type(ref) is int and ref>0 else p['base_price']
                # 대부분 공개 참고가와 같고 일부만 100원 차이: 모두 점포 확인 가격은 아니다.
                price=max(100,base+rng.choices((-100,0,100),(1,18,1))[0])
                stock=0 if rng.random()<.16 else rng.randint(2,24)
                promo=fact.get('promotion','') if fact else ''
                if promo not in ('1+1','2+1'):promo=''
                # 원본 조건은 별도 fact JSON에 보존. 회원/카드/교차 상품은 가정 계산에도 넣지 않는다.
                conditions=str(fact.get('conditions') or '') if fact else ''
                unsupported=any(t in conditions for t in ('회원 전용','카드 전용','교차증정 가능','교차 증정 가능','다른 상품 증정','멤버십 전용'))
                unsupported=unsupported or bool(fact and any(fact.get(k) is True for k in ('membership_required','card_required','cross_gift')))
                if unsupported:promo=''
                source_start=fact.get('start') if fact else None;source_end=fact.get('end') if fact else None
                # TEST 기간은 원본 기간과 다른 필드. 종료/예정 기간이 명시되면 해당 원본 기간을 따른다.
                start,end=source_start or '2026-11-01',source_end or '2026-11-30'
                try:
                    date.fromisoformat(start);date.fromisoformat(end)
                except (TypeError,ValueError):start,end='2026-11-01','2026-11-30'
                note=('공개 '+promo+' 표시 기반·동일 상품/점포 적용은 TEST 가정') if promo else '행사 미확인·TEST 일반 판매 가정'
                values=(store['id'],p['id'],price,stock,promo,start,end,1,1,
                  '공개 참고가 기반 TEST 점포 판매가','가상 재고',note,fact['source_url'] if fact else '',
                  source_start,source_end,fact.get('source_updated_at') if fact else None,
                  fact.get('retrieved_at') or FIXED_DATE if fact else '')
                offers.append(values)
        db.executemany('INSERT INTO offers VALUES ('+','.join('?' for _ in offers[0])+')',offers)
        db.commit()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT count(*) FROM offers').fetchone()[0]==7200
        db.execute('VACUUM')
    return output


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(build(args.output))
