from dataclasses import replace
from datetime import datetime, timezone
from core.models import StoreProduct
from core.cart_models import CartItem, StoreEvaluation, LineEvaluation
from .cart_pricing import evaluate_line
from .distance import haversine_km


def store_key(row: StoreProduct) -> str | None:
    if row.store_code:
        return f'{row.brand}|{row.store_code}'
    if row.address.strip():
        return f'{row.brand}|address:{row.address.strip().casefold()}'
    if row.latitude is not None and row.longitude is not None:
        return f'{row.brand}|position:{row.latitude:.6f},{row.longitude:.6f}'
    return None


def _observation_time(row):
    try:
        t=datetime.fromisoformat((row.stock_updated_at or row.updated_at).replace('Z','+00:00'))
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
    except (ValueError,TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _pick(rows):
    if not rows:
        return None,False
    recent=max(_observation_time(row) for row in rows)
    latest=[r for r in rows if _observation_time(r)==recent]
    facts={(r.price,r.promotion,r.promotion_known,r.stock,r.stock_known,
            r.stock_quantity_exact,r.data_source,r.price_data_status,r.promotion_data_status) for r in latest}
    return latest[0],len(facts)>1


def evaluate_stores(cart: list[CartItem], records_by_item: dict[str,list[StoreProduct]],
                    user_lat: float,user_lng: float,radius_km: float,max_stores: int=10) -> list[StoreEvaluation]:
    if not cart:
        return []
    representatives={}
    for rows in records_by_item.values():
        for row in rows:
            if (key:=store_key(row)) is not None:
                representatives.setdefault(key,row)
    evaluated=[]
    for key, representative in representatives.items():
        distance=haversine_km(user_lat,user_lng,representative.latitude,representative.longitude)
        if distance is not None and distance>radius_km:
            continue
        lines=[]
        for item in cart:
            row,conflict=_pick([r for r in records_by_item.get(item.item_id,[]) if store_key(r)==key])
            line=evaluate_line(item,replace(row,distance_km=distance) if row else None)
            if conflict:
                line=LineEvaluation(item.item_id,item.quantity,None,None,'UNKNOWN','UNKNOWN','동일 시각의 자료가 충돌합니다.',row)
            lines.append(line)
        sources={line.record.data_source for line in lines if line.record}
        source=next(iter(sources)) if len(sources)==1 else 'PARTIAL'
        known=sum(line.payable or 0 for line in lines)
        total=known if all(line.payable is not None for line in lines) else None
        amounts=[line.payable if line.payable is not None else line.reference_payable for line in lines]
        reference_total=sum(amounts) if all(x is not None for x in amounts) else None
        reasons=[f'{item.label}: {line.reason}' for item,line in zip(cart,lines)
                 if line.stock_state!='ENOUGH' or line.pricing_state!='CONFIRMED']
        if distance is None:
            reasons.append('위치 미확인: 검색 반경 안인지 확인할 수 없습니다.')
        if source not in ('TEST','LIVE'):
            reasons.append(f'{source} 자료: 현재 확정 추천에서 제외')
        eligible=distance is not None and total is not None and source in ('TEST','LIVE') and all(
            line.stock_state=='ENOUGH' and line.pricing_state=='CONFIRMED' for line in lines)
        evaluated.append(StoreEvaluation(key,representative.brand,representative.store_name,distance,
                                         lines,total,known,reference_total,eligible,source,reasons))
    return sorted(evaluated,key=lambda r:(r.distance_km if r.distance_km is not None else float('inf'),r.store_key))[:max_stores]

