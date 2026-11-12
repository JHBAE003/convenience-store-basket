"""두 데이터 공급자를 같은 검색/조회 계약으로 연결한다. TEST는 네트워크를 쓰지 않는다."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
import sys
import time

from planner.engine import Product, Store, Offer, haversine_km
from planner.test_data import TestDataset, DEFAULT_PRODUCTS

KST = timezone(timedelta(hours=9))
BRANDS = ('CU', 'GS25', '세븐일레븐', '이마트24')


def korean_today():
    return datetime.now(KST).date().isoformat()


@dataclass(frozen=True)
class Candidate:
    id: str
    brand: str
    name: str
    spec: str
    product_code: str = ''
    barcode: str = ''
    reference_price: int | None = None
    seed_id: str = ''
    record: object = field(default=None, repr=False, compare=False)


@dataclass
class Search:
    candidates: tuple[Candidate, ...] = ()
    messages: tuple[str, ...] = ()


def group_search_candidates(candidates):
    """식별이 확실한 가상 상품만 브랜드별 중복을 합친다. 이름 유사도로 묶지 않는다."""
    groups={}
    for c in candidates:
        key=('test',c.seed_id,c.spec) if c.seed_id else ('candidate',c.id)
        groups.setdefault(key,[]).append(c)
    return tuple(tuple(group) for group in groups.values())


@dataclass
class Snapshot:
    stores: tuple[Store, ...] = ()
    offers: tuple[Offer, ...] = ()
    observations: tuple[dict, ...] = ()
    messages: tuple[str, ...] = ()
    context: tuple = ()
    loaded_at: float = field(default_factory=time.monotonic)
    ttl: int | None = 60

    def usable(self, context, now=None):
        age = (time.monotonic() if now is None else now) - self.loaded_at
        return self.context == context and (self.ttl is None or 0 <= age < self.ttl)


def query_context(data_mode, products, links, origin, brands):
    # 수량·예산 변경은 같은 관측으로 재계산한다. 상품·연결·위치·브랜드 변경은 별도 조회다.
    return (data_mode, 'real-catalogue-v322' if data_mode == 'TEST' else None,
            tuple(sorted(p.id for p in products)),
            tuple(sorted((pid, c.id) for pid, cs in links.items() for c in cs)),
            tuple(origin), tuple(sorted(brands)))


def calculation_scope(snapshot, settings):
    """계산 한도는 선택 반경 적용 후 판단한다. 받은 원본 자료는 자르지 않는다."""
    stores = tuple(s for s in snapshot.stores if s.brand in settings.brands
                   and haversine_km(*settings.origin,s.lat,s.lng) <= settings.radius_km)
    if len(stores)>24:
        return (), (), f'선택 반경 안에 매장 {len(stores)}곳이 있습니다. 계산 한도 24곳에 맞춰 반경이나 브랜드를 좁혀 주세요. 추가 조회 없이 다시 계산합니다.'
    ids={s.id for s in stores}
    return stores, tuple(o for o in snapshot.offers if o.store_id in ids), ''


class TestProvider:
    def __init__(self, path=None):
        self.dataset = TestDataset() if path is None else TestDataset(path)
        self.metadata = self.dataset.metadata()

    @staticmethod
    def _candidate(row):
        return Candidate(f'T:{row["brand"]}:{row["id"]}', row['brand'], row['name'], row['spec'],
                         product_code=row['product_code'], barcode=row['barcode'],
                         reference_price=row['base_price'], seed_id=row['id'])

    def defaults(self):
        defaults=tuple(self.metadata.get('default_products',DEFAULT_PRODUCTS))
        rows=self.dataset.search('',BRANDS,defaults)
        by_id={row['id']:Product(row['id'],row['name'],row['spec']) for row in rows}
        products=tuple(by_id[id] for id in defaults)
        links={p.id:[self._candidate(r) for r in rows if r['id']==p.id] for p in products}
        origin=self.metadata['origin']
        return products,links,(origin['lat'],origin['lng'])

    def search(self, keyword, origin, brands):
        rows=self.dataset.search(keyword,brands)
        messages=['공개 목록의 실제 상품명·행사 표시를 참고한 TEST입니다. 점포별 판매 여부·판매가·재고와 미확인 행사 조건은 가정입니다.']
        return Search(tuple(self._candidate(r) for r in rows),tuple(messages))

    def lookup(self, products, links, origin, brands, context, force=False):
        stores={r['id']:r for r in self.dataset.stores()}
        allowed={(pid,c.brand,c.product_code) for pid,cs in links.items() for c in cs if c.brand in brands}
        canonical={p.id:p for p in products}
        # 검색 후보의 합성 내부 코드까지 일치하는 연결만 사용한다.
        ids={c.seed_id:pid for pid,cs in links.items() for c in cs if pid in canonical}
        rows=self.dataset.offers(tuple(ids),brands)
        codes={(r['id'],r['brand']):r['product_code'] for r in self.dataset.search('',brands,tuple(ids))}
        offers=[];observations=[]
        for r in rows:
            pid=ids[r['product_id']]
            if (pid,r['brand'],codes[r['product_id'],r['brand']]) not in allowed:continue
            p=canonical[pid]
            if r['spec']!=p.spec:continue
            store=stores[r['store_id']]
            offers.append(Offer(r['store_id'],pid,r['price'],r['stock'],r['promotion'],r['start'],r['end'],bool(r['conditions_verified']),p.spec))
            observations.append({'브랜드':store['brand'],'점포 코드':store['id'],'상품 ID':p.id,
                '매장':store['name'],'상품':p.name,'판매가':f'{r["price"]:,}원',
                '가격 상태':r['price_basis'],'재고':str(r['stock'])+' (가상)','행사':r['promotion'] or '일반 판매 (TEST 가정)',
                '앱 수신 시각':'저장된 TEST 파일','원본 자료 시각':'해당 없음 (TEST 판매가·재고)','앱 캐시':'로컬 파일',
                '행사 적용 근거':r['promotion_basis'],'행사 참고 출처':r['promotion_source_url'],
                '참고 자료 확인 시각':r['source_checked_at'],'참고 원본 갱신 시각':r['source_updated_at'] or '미제공',
                '점포 위치 출처':store['source_kind'],'점포 확인일':store['checked_at'][:10]})
        used={o.store_id for o in offers}
        return Snapshot(tuple(Store(s['id'],s['brand'],s['name'],s['lat'],s['lng']) for s in stores.values() if s['id'] in used),
                        tuple(offers),tuple(observations),('실제 상품·공개 행사 참고 TEST입니다. 점포별 판매 여부·판매가·재고·미확인 조건은 가정이며 현재 영업·취급·재고를 보장하지 않습니다.',),context,ttl=None)


def load_legacy():
    """보존 코드의 core 절대 import를 한 곳에서만 설정한다. TEST에서는 호출하지 않는다."""
    root = Path(__file__).resolve().parents[1] / 'legacy_v211'
    loaded = sys.modules.get('core')
    if loaded and Path(loaded.__file__).resolve().parent != root / 'core':
        raise RuntimeError('다른 버전의 수집 코드가 이미 실행 중입니다. 앱을 다시 시작해 주세요.')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def candidate_from_row(row):
    spec = ' · '.join(str(x) for x in (row.manufacturer, row.volume, row.flavor, row.pack_count) if x)
    spec = spec or row.product_name + ' · 세부 규격 미제공'
    identity = '\x1f'.join((row.brand, row.product_code, row.barcode, row.product_name, spec))
    return Candidate('L:' + sha256(identity.encode()).hexdigest()[:20], row.brand,
                     row.product_name, spec, row.product_code, row.barcode,
                     row.price if row.price > 0 else None, record=row)


def link_conflict(existing, candidate):
    if existing.record is not None and candidate.record is not None:
        load_legacy()
        from core.services.product_matching import conflicting_mapping_variants
        return conflicting_mapping_variants(existing.record, candidate.record)
    return existing.spec != candidate.spec


class LiveProvider:
    def __init__(self, manager=None):
        self._manager = manager
        self._directory = None

    def manager(self):
        if self._manager is None:
            load_legacy()
            from core.services.query_service import new_session_manager
            self._manager, self._directory = new_session_manager('LIVE')
        return self._manager

    def search(self, keyword, origin, brands):
        try:
            rows, statuses = self.manager().search_products(keyword, *origin, list(brands), 'LIVE')
            candidates = {c.id: c for c in (candidate_from_row(r) for r in rows)}
            return Search(tuple(candidates.values()), tuple(f'{s.brand}: {s.message}' for s in statuses))
        except Exception:
            return Search(messages=('실시간 상품 검색에 실패했습니다. 잠시 후 다시 시도해 주세요. 시연 자료로 대체하지 않습니다.',))

    def lookup(self, products, links, origin, brands, context, force=False):
        started = time.monotonic()
        load_legacy()
        from core.cart_models import CatalogLink
        from core.services.product_matching import same_product
        from core.services.promotion import promotion_applicability, normalize_promotion
        try:
            manager = self.manager()
        except Exception:
            return Snapshot(messages=('실시간 조회를 준비하지 못했습니다. 앱을 다시 실행해 주세요. 시연 데이터로 대체하지 않습니다.',),context=context)
        observations, pending, store_map, messages = [], {}, {}, []
        ttl = manager.cache_ttl_seconds
        for p in products:
            for c in links.get(p.id, ()):
                if c.brand not in brands:
                    continue
                link = CatalogLink(p.id, c.brand, c.barcode, c.product_code, c.name)
                try:
                    rows, statuses = manager.search_selected(link, *origin, 'LIVE', include_unpriced=True, force_refresh=force)
                except Exception:
                    messages.append(f'{c.brand} · {p.name}: 조회에 실패했습니다. 시연 자료로 대체하지 않습니다.')
                    continue
                messages.extend(f'{s.brand} · {p.name}: {s.message}' for s in statuses)
                source_bad = any(s.source in ('CACHE', 'ERROR') for s in statuses)
                for status in statuses:
                    if status.cache_age_seconds is not None:
                        ttl = min(ttl, max(0, int(manager.cache_ttl_seconds - status.cache_age_seconds)))
                for row in rows:
                    if row.brand != c.brand or c.record is None or not same_product(c.record, row):
                        messages.append(f'{c.brand} · {p.name}: 상품 식별이 일치하지 않는 결과는 제외했습니다.')
                        continue
                    stock_ok = (not source_bad and row.data_source not in ('CACHE', 'ERROR', 'TEST')
                                and row.stock_data_status == 'LIVE' and row.stock_known and row.stock_quantity_exact
                                and type(row.stock) is int and row.stock >= 0)
                    price_ok = (not source_bad and row.price_data_status == 'LIVE' and type(row.price) is int and row.price > 0)
                    # 기간·조건이 확인되지 않은 행사는 엔진에서 일반 판매로 계산한다.
                    promo_ok = row.promotion_data_status == 'LIVE' and promotion_applicability(row)[0]
                    observations.append({'브랜드':row.brand,'점포 코드':row.store_code or row.store_name,'상품 ID':p.id,
                        '매장': row.store_name, '상품': p.name,
                        '판매가': f'{row.price:,}원' if row.price > 0 else '미확인',
                        '가격 상태': '점포 확인' if price_ok else '참고/미확인 · 추천 제외',
                        '재고': str(row.stock) if stock_ok else ('있음 · 수량 미확인' if row.stock_known and not row.stock_quantity_exact else '미확인/과거 자료'),
                        '행사': row.promotion or ('없음' if row.promotion_known else '미확인'),
                        '앱 수신 시각': row.received_at or '미제공',
                        '원본 자료 시각': row.stock_source_updated_at or row.source_updated_at or '미제공',
                        '앱 캐시': '사용' if row.app_cache_used else '사용 안 함'})
                    lat, lng = row.latitude, row.longitude
                    if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in (lat, lng))
                            or not -90 <= lat <= 90 or not -180 <= lng <= 180):
                        messages.append(f'{row.brand} · {row.store_name}: 위치 미확인으로 구매안 계산에서 제외했습니다.')
                        continue
                    identity = (row.brand, row.store_code or (row.store_name, row.address))
                    sid = 'S:' + sha256(repr(identity).encode()).hexdigest()[:16]
                    store_map[sid] = Store(sid, row.brand, row.store_name, lat, lng)
                    offer = Offer(sid, p.id, row.price if price_ok else None, row.stock if stock_ok else None,
                                  normalize_promotion(row.promotion), row.promotion_start_date, row.promotion_end_date, promo_ok, p.spec)
                    key = (sid, p.id)
                    if key in pending and pending[key] != offer:
                        pending[key] = Offer(sid, p.id, None, None, spec=p.spec)
                        messages.append(f'{row.store_name} · {p.name}: 서로 다른 응답이 있어 추천에서 제외했습니다.')
                    else:
                        pending[key] = offer
        return Snapshot(tuple(store_map.values()), tuple(pending.values()), tuple(observations),
                        tuple(dict.fromkeys(messages)), context, loaded_at=started, ttl=ttl)
