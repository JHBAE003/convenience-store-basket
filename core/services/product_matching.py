from __future__ import annotations

import re
from core.models import StoreProduct


def normalize_product_name(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").casefold())


def same_product(left: StoreProduct, right: StoreProduct) -> bool:
    """Conservative identity check: verified identifiers first, then exact names."""
    if left.brand != right.brand:
        return False
    if left.barcode and right.barcode:
        return left.barcode.strip() == right.barcode.strip()
    left_codes = {code.strip() for code in (left.barcode, left.product_code) if code and code.strip()}
    right_codes = {code.strip() for code in (right.barcode, right.product_code) if code and code.strip()}
    if left_codes and right_codes:
        return bool(left_codes & right_codes)
    # Some event catalogs omit identifiers. Require the complete normalized
    # name; a partial keyword is never enough to attach an event to inventory.
    name = normalize_product_name(left.product_name)
    return bool(name) and name == normalize_product_name(right.product_name)
