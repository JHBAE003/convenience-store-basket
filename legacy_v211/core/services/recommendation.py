from __future__ import annotations

from typing import Iterable

from core.models import Recommendation, StoreProduct


def _normalize_inverse(value: float, maximum: float) -> float:
    if maximum <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - value / maximum))


def recommend(records: Iterable[StoreProduct], mode: str = "종합추천") -> Recommendation:
    candidates = [x for x in records if x.stock_known and x.stock > 0 and x.price > 0]
    if not candidates:
        return Recommendation(None, "현재 재고가 있는 상품을 찾지 못했습니다.")

    if mode == "최저가":
        priced = [x for x in candidates if x.promotion_unit_price is not None]
        if not priced:
            return Recommendation(None, "행사 조건 또는 실구매가를 확인할 수 없어 최저가를 비교하지 못했습니다.")
        item = min(priced, key=lambda x: x.promotion_unit_price)
        return Recommendation(item, "재고가 있는 매장 중 실질 개당 가격이 가장 낮습니다.")

    if mode == "가장 가까운 곳":
        with_distance = [x for x in candidates if x.distance_km is not None]
        if not with_distance:
            return Recommendation(candidates[0], "거리 데이터가 없어 첫 번째 재고 보유 매장을 표시했습니다.")
        item = min(with_distance, key=lambda x: x.distance_km or float("inf"))
        return Recommendation(item, "재고가 있는 매장 중 현재 위치에서 가장 가깝습니다.")

    if mode == "재고 우선":
        item = max(candidates, key=lambda x: x.stock)
        return Recommendation(item, "재고 수량이 가장 많은 매장입니다.")

    # 종합추천: 판매 가격 45%, 거리 35%, 재고 20%.
    # 행사 혜택은 별도의 비교 항목에 남기고 종합 점수에는 반영하지 않는다.
    max_price = max(x.price for x in candidates) or 1
    max_distance = max((x.distance_km or 0) for x in candidates) or 1
    max_stock = max(x.stock for x in candidates) or 1

    scored = []
    for x in candidates:
        price_component = _normalize_inverse(x.price, max_price)
        distance_component = _normalize_inverse(x.distance_km or 0, max_distance) if x.distance_km is not None else 0.5
        stock_component = x.stock / max_stock
        score = (
            price_component * 0.45
            + distance_component * 0.35
            + stock_component * 0.20
        )
        scored.append((score, x))

    _, item = max(scored, key=lambda pair: pair[0])
    return Recommendation(item, "판매 가격 45%·거리 35%·재고 20%를 반영한 종합 추천입니다.")
