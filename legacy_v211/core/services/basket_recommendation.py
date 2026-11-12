from core.cart_models import StoreEvaluation, BasketRecommendations


def recommend_baskets(stores:list[StoreEvaluation],budget:int) -> BasketRecommendations:
    if budget<0:
        raise ValueError('예산은 0원 이상이어야 합니다.')
    candidates=[s for s in stores if s.eligible and s.total is not None]
    if not candidates:
        return BasketRecommendations(None,None,None,'필요 수량과 가격·행사를 모두 확인한 매장이 없습니다.')
    within=[s for s in candidates if budget==0 or s.total<=budget]
    selected=within or candidates
    cheapest=min(selected,key=lambda s:(s.total,s.distance_km if s.distance_km is not None else float('inf'),s.store_name,s.store_key))
    nearby=[s for s in selected if s.distance_km is not None]
    nearest=min(nearby,key=lambda s:(s.distance_km,s.total,s.store_name,s.store_key)) if nearby else None
    excess=cheapest.total-budget if budget and cheapest.total>budget else None
    message=f'비교한 {len(stores)}개 매장 중 조건 확인 {len(candidates)}곳, 예산 조건 충족 {len(within)}곳'
    if excess:
        message+=f'. 예산을 충족하는 매장이 없어 최소 {excess:,}원 초과합니다.'
    return BasketRecommendations(cheapest,nearest,excess,message)
