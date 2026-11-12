"""Exact subset enumeration with per-product allocation and used-store-mask DP."""
from dataclasses import dataclass
from datetime import date
from itertools import combinations, permutations
from math import isfinite
from functools import lru_cache

from legacy_v211.core.services.distance import haversine_km
from legacy_v211.core.services.promotion import calculate_purchase_cost

MODES = ('최저 결제액', '한 곳에서 구매', '최단 이동', '가격·거리 균형', '예산 내 구매')

@dataclass(frozen=True)
class Product:
    id: str
    name: str
    spec: str

@dataclass(frozen=True)
class Store:
    id: str
    brand: str
    name: str
    lat: float
    lng: float

@dataclass(frozen=True)
class Offer:
    store_id: str
    product_id: str
    price: int | None
    stock: int | None
    promotion: str = ''
    start: str = '2026-11-01'
    end: str = '2026-11-30'
    conditions_verified: bool = True
    spec: str = ''

@dataclass(frozen=True)
class Settings:
    origin: tuple[float, float] = (37.5665, 126.978)
    brands: tuple[str, ...] = ('CU', 'GS25', '세븐일레븐', '이마트24')
    max_stores: int = 3
    radius_km: float = 5.0
    route_limit_km: float = 20.0
    return_home: bool = False
    budget: int = 10000
    strict_budget: bool = False
    distance_weight: int = 1000
    visit_weight: int = 500
    as_of: str = '2026-11-15'

@dataclass(frozen=True)
class Line:
    store_id: str
    product_id: str
    quantity: int
    unit_price: int
    promotion: str
    amount: int
    note: str

@dataclass(frozen=True)
class Plan:
    lines: tuple[Line, ...]
    route: tuple[str, ...]
    distance_km: float
    total: int
    score: float

    @property
    def count(self): return len(self.route)

    @property
    def signature(self):
        return tuple((x.store_id, x.product_id, x.quantity) for x in self.lines)

@dataclass
class Result:
    plans: tuple[Plan, ...]
    winners: dict[str, Plan | None]
    shortages: dict[str, int]
    partial: Plan | None
    reasons: tuple[str, ...]
    combinations_checked: int

def valid_int(v, low=0): return type(v) is int and v >= low

def promotion_state(offer, as_of):
    today = date.fromisoformat(as_of)
    if not offer.promotion: return '', '행사 없음 · 일반 판매'
    if not offer.conditions_verified or offer.promotion not in ('1+1', '2+1'):
        return '', '조건 미확인/미지원 · 할인 미적용'
    try:
        start, end = date.fromisoformat(offer.start), date.fromisoformat(offer.end)
    except (ValueError, TypeError): return '', '행사 기간 미확인 · 할인 미적용'
    if start > end: return '', '행사 기간 오류 · 할인 미적용'
    if today > end: return '', '종료된 행사 · 일반 판매'
    if today < start: return '', '시작 전 행사 · 일반 판매'
    return offer.promotion, '동일 상품 행사 적용'

def cost(offer, quantity, as_of):
    if quantity == 0: return 0
    if not valid_int(offer.price, 1) or not valid_int(quantity, 1):
        raise ValueError('가격과 수량을 확인해 주세요.')
    promo, _ = promotion_state(offer, as_of)
    return calculate_purchase_cost(offer.price, promo, quantity).payable

def allocations(total, caps):
    """Bounded integer compositions for ONE item, not a cross-item product."""
    if len(caps) == 1:
        if total <= caps[0]: yield (total,)
        return
    low = max(0, total-sum(caps[1:]))
    for q in range(low, min(total, caps[0])+1):
        for tail in allocations(total-q, caps[1:]): yield (q,)+tail

def route_for(stores, settings, distances=None):
    best = None
    for order in permutations(sorted(stores, key=lambda s:s.id)):
        if distances is None:
            points = [settings.origin]+[(s.lat,s.lng) for s in order]
            if settings.return_home: points.append(settings.origin)
            distance = sum(haversine_km(*a,*b) for a,b in zip(points,points[1:]))
        else:
            ids=['@origin']+[s.id for s in order]+(['@origin'] if settings.return_home else [])
            distance=sum(distances.get((a,b),float('inf')) for a,b in zip(ids,ids[1:]))
        candidate = (distance, tuple(s.id for s in order))
        if best is None or candidate < best: best = candidate
    return best[1], best[0]

def rank(plan, mode):
    d = round(plan.distance_km, 9)
    if mode == MODES[0]: key = (plan.total,d,plan.count)
    elif mode == MODES[1]: key = (plan.total,d)
    elif mode == MODES[2]: key = (d,plan.total,plan.count)
    elif mode == MODES[3]: key = (round(plan.score,6),plan.count,plan.total)
    else: key = (d,plan.total,plan.count)
    return (*key,plan.route,plan.signature)

def _validate(products, stores, offers, cart, s):
    if len(products)>6 or len(stores)>24 or len(cart)>6: raise ValueError('최대 상품 6종·매장 24곳까지 지원합니다.')
    if len({p.id for p in products})!=len(products) or len({x.id for x in stores})!=len(stores):
        raise ValueError('상품·매장 ID가 중복됩니다.')
    ids={p.id for p in products}
    if any(k not in ids or not valid_int(v,1) or v>12 for k,v in cart.items()):
        raise ValueError('상품별 요청 수량은 1~12개입니다.')
    if len({(o.store_id,o.product_id) for o in offers})!=len(offers): raise ValueError('판매 정보가 중복됩니다.')
    if not valid_int(s.max_stores,1) or s.max_stores>3: raise ValueError('최대 방문 수는 1~3곳입니다.')
    if any(not valid_int(v) for v in (s.budget,s.distance_weight,s.visit_weight)):
        raise ValueError('예산·부담값은 0 이상의 정수입니다.')
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not isfinite(v) or v<0
           for v in (s.radius_km,s.route_limit_km)): raise ValueError('거리는 유효한 0 이상 값이어야 합니다.')
    def coordinate(point):
        return len(point)==2 and all(isinstance(v,(int,float)) and not isinstance(v,bool) and isfinite(v) for v in point) and -90<=point[0]<=90 and -180<=point[1]<=180
    if not coordinate(s.origin) or any(not coordinate((x.lat,x.lng)) for x in stores): raise ValueError('좌표 범위를 확인해 주세요.')
    date.fromisoformat(s.as_of)

def partial_budget_examples(selected, available, cart, settings, distances=None):
    """Feasible examples only: greedy marginal cost, never claimed optimal.

    Full-quantity recommendations use the exact DP. This optional fallback
    never edits the requested cart and explicitly reports missing quantities.
    """
    examples=[]
    for n in range(1,min(settings.max_stores,len(selected))+1):
        for subset in combinations(selected,n):
            route,distance=route_for(subset,settings,distances)
            if distance>settings.route_limit_km:continue
            quantities={};delivered={pid:0 for pid in cart};amount=0
            while True:
                options=[]
                for st in subset:
                    for pid,target in sorted(cart.items()):
                        o=available.get((st.id,pid));qty=quantities.get((st.id,pid),0)
                        if o is None or delivered[pid]>=target or qty>=o.stock:continue
                        extra=cost(o,qty+1,settings.as_of)-cost(o,qty,settings.as_of)
                        if amount+extra<=settings.budget:options.append((extra,pid,st.id,o))
                if not options:break
                extra,pid,sid,o=min(options,key=lambda x:x[:3])
                quantities[sid,pid]=quantities.get((sid,pid),0)+1;delivered[pid]+=1;amount+=extra
            used=[st for st in subset if any(sid==st.id for sid,pid in quantities)]
            if not used or all(delivered[k]==cart[k] for k in cart):continue
            route,distance=route_for(used,settings,distances)
            if distance>settings.route_limit_km:continue
            lines=[]
            for (sid,pid),qty in sorted(quantities.items()):
                o=available[sid,pid];promo,note=promotion_state(o,settings.as_of)
                lines.append(Line(sid,pid,qty,o.price,promo,cost(o,qty,settings.as_of),note))
            examples.append(Plan(tuple(lines),route,distance,amount,amount+distance*settings.distance_weight+(len(used)-1)*settings.visit_weight))
    return examples

def solve(products, stores, offers, cart, settings=Settings(), distance_matrix=None):
    _validate(products,stores,offers,cart,settings)
    distances=None
    if distance_matrix is not None:
        distances={}
        for a,b,value in distance_matrix:
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not value>=0:
                raise ValueError('경로 거리는 0 이상이어야 합니다. 연결되지 않은 경로는 무한대로 표시합니다.')
            if (a,b) in distances:raise ValueError('경로 거리 자료에 중복이 있습니다.')
            distances[a,b]=value
    if not cart:return Result((),dict.fromkeys(MODES),{},None,('상품을 한 개 이상 담아 주세요.',),0)
    spec={p.id:p.spec for p in products}
    selected=sorted([s for s in stores if s.brand in settings.brands and
                     haversine_km(*settings.origin,s.lat,s.lng)<=settings.radius_km+1e-12],key=lambda s:s.id)
    # Unknown stock, unknown prices, and conflicting sizes are never purchase capacity.
    available={(o.store_id,o.product_id):o for o in offers if o.product_id in spec and
               o.spec==spec[o.product_id] and valid_int(o.stock) and valid_int(o.price,1)}
    def cap(st,pid):return min(12,available[(st.id,pid)].stock) if (st.id,pid) in available else 0
    # 점포별 동일 수량의 행사 계산을 수천 개 매장 조합마다 반복하지 않는다.
    lines_by_quantity={}
    for (sid,pid),o in available.items():
        if pid not in cart:continue
        promo,note=promotion_state(o,settings.as_of)
        for qty in range(1,min(o.stock,cart[pid])+1):
            lines_by_quantity[sid,pid,qty]=Line(sid,pid,qty,o.price,promo,cost(o,qty,settings.as_of),note)
    @lru_cache(maxsize=1024)
    def bounded_choices(target,caps):return tuple(allocations(target,caps))
    shortages={pid:max(0,q-sum(cap(st,pid) for st in selected)) for pid,q in cart.items()}
    plans=[]; partials=[]; checked=0; route_blocked=0
    for n in range(1,min(settings.max_stores,len(selected))+1):
        for subset in combinations(selected,n):
            checked+=1
            route,dist=route_for(subset,settings,distances)
            if dist>settings.route_limit_km+1e-12:
                route_blocked+=1;continue
            # Track the cheapest allocation per used-store mask after each item.
            dp={0:(0,())}; delivered={}
            for pid,q in sorted(cart.items()):
                caps=tuple(cap(st,pid) for st in subset)
                target=min(q,sum(caps));delivered[pid]=target
                # 같은 품목·사용 매장 집합이면 최소 결제액 배분 하나만 다음 단계에 필요하다.
                item_choices={}
                for quantities in bounded_choices(target,caps):
                    used=0;extra=[];subtotal=0
                    for idx,(st,qty) in enumerate(zip(subset,quantities)):
                        if not qty:continue
                        line=lines_by_quantity[st.id,pid,qty]
                        extra.append(line);subtotal+=line.amount;used|=1<<idx
                    tie=tuple((x.store_id,x.product_id,x.quantity) for x in extra)
                    old=item_choices.get(used)
                    if old is None or (subtotal,tie)<(old[0],old[2]):
                        item_choices[used]=(subtotal,tuple(extra),tie)
                nxt={}
                for mask,(amount,lines) in dp.items():
                    for used,(subtotal,extra,_) in item_choices.items():
                        newmask=mask|used
                        newlines=tuple(sorted(lines+extra,key=lambda x:(x.store_id,x.product_id)))
                        candidate=(amount+subtotal,newlines)
                        old=nxt.get(newmask)
                        tie=tuple((x.store_id,x.product_id,x.quantity) for x in newlines)
                        if old is None or (candidate[0],tie)<(old[0],tuple((x.store_id,x.product_id,x.quantity) for x in old[1])):
                            nxt[newmask]=candidate
                dp=nxt
            final=dp.get((1<<n)-1)
            if final is None:continue
            amount,lines=final
            plan=Plan(lines,route,dist,amount,amount+dist*settings.distance_weight+(n-1)*settings.visit_weight)
            if all(delivered[k]==cart[k] for k in cart):plans.append(plan)
            elif sum(delivered.values())>0:partials.append(plan)
    plans=sorted(plans,key=lambda p:rank(p,MODES[0]))
    eligible=[p for p in plans if not settings.strict_budget or p.total<=settings.budget]
    winners={}
    for mode in MODES:
        pool=[p for p in eligible if (mode!=MODES[1] or p.count==1) and
              (mode!=MODES[4] or p.total<=settings.budget)]
        winners[mode]=min(pool,key=lambda p:rank(p,mode),default=None)
    reasons=[]
    if not selected:reasons.append('선택 브랜드와 출발지 반경 안에 매장이 없습니다.')
    if any(shortages.values()):reasons.append('반경·브랜드 조건 안의 확인된 전체 재고가 부족합니다.')
    if not plans and selected:reasons.append(f'최대 {settings.max_stores}곳 방문·전체 이동거리 제한으로 전체 수량을 충족하지 못합니다.')
    if route_blocked:reasons.append(f'매장 조합 {route_blocked}개가 이동거리 제한을 초과했거나 연결 가능한 경로가 없습니다.')
    if settings.strict_budget and plans and not eligible:
        reasons.append(f'전체 수량 구매의 최소 결제액 {min(p.total for p in plans):,}원이 예산 {settings.budget:,}원을 초과합니다.')
    if settings.strict_budget and not plans:
        reasons.append(f'예산 {settings.budget:,}원 상한도 적용했습니다. 예산 때문에 일부 구매안도 없을 수 있습니다.')
    if not any(p.count==1 for p in eligible):reasons.append('현재 조건으로 한 곳에서 모두 구매할 수 없습니다. 2·3곳 대안을 확인하세요.')
    partial_pool=[p for p in partials if not settings.strict_budget or p.total<=settings.budget]
    if settings.strict_budget and not eligible:
        partial_pool.extend(partial_budget_examples(selected,available,cart,settings,distances))
    partial=min(partial_pool,key=lambda p:(-sum(x.quantity for x in p.lines),*rank(p,MODES[0])),default=None)
    return Result(tuple(eligible),winners,shortages,partial,tuple(reasons),checked)
