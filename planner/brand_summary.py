"""점포별 관측은 유지하고 화면용 브랜드 요약만 만든다."""
from collections import defaultdict
from planner.engine import cost,promotion_state,valid_int


def price_range(values):
    prices=sorted(set(values))
    if not prices:return '미확인'
    if len(prices)==1:return f'{prices[0]:,}원'
    return f'{prices[0]:,}~{prices[-1]:,}원'


def brand_offer_rows(stores,offers,products,cart,settings):
    by_store={s.id:s for s in stores};by_product={p.id:p for p in products};groups=defaultdict(dict)
    for offer in offers:
        store=by_store.get(offer.store_id)
        if store and store.brand in settings.brands and offer.product_id in cart:
            groups[store.brand,offer.product_id][store.id]=offer
    rows=[]
    for (brand,pid),by_id in sorted(groups.items()):
        values=list(by_id.values());qty=cart[pid]
        known=[o for o in values if valid_int(o.stock)]
        eligible=[o for o in known if o.stock>=qty and valid_int(o.price,1) and o.spec==by_product[pid].spec]
        promos=sorted({promotion_state(o,settings.as_of)[0] for o in values}-{''})
        rows.append({'브랜드':brand,'상품':by_product[pid].name,'규격':by_product[pid].spec,
                     '확인 점포 수':len(values),'확인 가격 범위':price_range(o.price for o in values if valid_int(o.price,1)),
                     '재고 있음 (곳)':sum(o.stock>0 for o in known),'재고 0 (곳)':sum(o.stock==0 for o in known),
                     '수량 미확인 (곳)':len(values)-len(known),'요청 수량':qty,'요청 수량 구매 가능 (곳)':len(eligible),
                     '요청 수량 결제액 범위':price_range(cost(o,qty,settings.as_of) for o in eligible),
                     '계산 가능한 행사':', '.join(promos) or '일반 판매로 계산'})
    return rows


def observation_rows(observations,stores):
    brands=defaultdict(set)
    for store in stores:brands[store.name].add(store.brand)
    groups=defaultdict(list)
    for row in observations:
        brand=row.get('브랜드')
        if not brand:
            matched=brands.get(row.get('매장'),set())
            brand=next(iter(matched)) if len(matched)==1 else '브랜드 미확인'
        groups[brand,row.get('상품 ID',row['상품'])].append(row)
    def received_values(rows,key):
        values={str(r.get(key) or '미제공') for r in rows}
        if '시각' not in key:return ' / '.join(sorted(values))
        provided=values-{'미제공','미확인'}
        if not provided:return '미제공'
        label=next(iter(provided)) if len(provided)==1 else f'여러 시각 ({len(provided)}개)'
        return label+(' · 일부 미제공' if provided!=values else '')
    return [{'브랜드':brand,'상품':rows[0]['상품'],
             '확인 점포 수':len({r.get('점포 코드') or r['매장'] for r in rows}),
             **{key:received_values(rows,key) for key in ('앱 수신 시각','원본 자료 시각','앱 캐시')}}
            for (brand,pid),rows in sorted(groups.items())]
