from __future__ import annotations

from core.models import StoreProduct


def same_store(left: StoreProduct, right: StoreProduct) -> bool:
    if left.brand != right.brand:
        return False
    if left.store_code and right.store_code:
        return left.store_code == right.store_code
    if left.address and right.address:
        return left.address.strip().casefold() == right.address.strip().casefold()
    if None not in (left.latitude, left.longitude, right.latitude, right.longitude):
        return abs(left.latitude - right.latitude) <= 0.00015 and abs(left.longitude - right.longitude) <= 0.00015
    return False
