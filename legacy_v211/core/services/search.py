from __future__ import annotations

from typing import Iterable

from core.models import StoreProduct
from core.services.distance import haversine_km
from core.services.promotion import normalize_promotion


def search_products(
    records: Iterable[StoreProduct],
    keyword: str,
    selected_brands: set[str],
    user_lat: float | None,
    user_lng: float | None,
    radius_km: float | None,
    include_out_of_stock: bool = True,
) -> list[StoreProduct]:
    key = keyword.strip().lower()
    result: list[StoreProduct] = []

    for item in records:
        if selected_brands and item.brand not in selected_brands:
            continue
        haystack = f"{item.product_name} {item.barcode}".lower()
        if key and key not in haystack:
            continue
        if not include_out_of_stock and item.stock <= 0:
            continue
        if user_lat is not None and user_lng is not None:
            calculated_distance = haversine_km(user_lat, user_lng, item.latitude, item.longitude)
            # Keep a distance supplied by a data source when store coordinates
            # are absent; otherwise calculate a consistent straight-line distance.
            if calculated_distance is not None:
                item.distance_km = calculated_distance
            if radius_km is not None and item.brand == "이마트24" and item.distance_km is None:
                continue
            if radius_km is not None and item.distance_km is not None and item.distance_km > radius_km:
                continue
        item.promotion = normalize_promotion(item.promotion)
        result.append(item)
    return result


def sort_records(records: list[StoreProduct], sort_key: str) -> list[StoreProduct]:
    if sort_key == "가격순":
        return sorted(records, key=lambda x: (x.promotion_unit_price or float("inf"), x.distance_km or float("inf")))
    if sort_key == "거리순":
        return sorted(records, key=lambda x: (x.distance_km if x.distance_km is not None else float("inf"), x.promotion_unit_price or float("inf")))
    if sort_key == "재고순":
        return sorted(records, key=lambda x: (-x.stock, x.promotion_unit_price or float("inf")))
    if sort_key == "행사순":
        return sorted(records, key=lambda x: (0 if x.promotion else 1, x.promotion_unit_price or float("inf")))
    if sort_key == "최저가":
        return sorted(records, key=lambda x: (0 if x.stock > 0 else 1, x.promotion_unit_price or float("inf")))
    if sort_key == "가장 가까운 곳":
        return sorted(records, key=lambda x: (0 if x.stock > 0 else 1, x.distance_km if x.distance_km is not None else float("inf")))
    if sort_key == "재고 우선":
        return sorted(records, key=lambda x: (-x.stock, x.promotion_unit_price or float("inf")))
    return records
