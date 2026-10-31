from dataclasses import dataclass

from core.cart_models import CartItem, LineEvaluation
from core.services.promotion import normalize_promotion
from core.services.v2_common import confirmed_cost


@dataclass(frozen=True)
class QuantitySuggestion:
    item_id: str
    original_quantity: int
    suggested_quantity: int
    original_total: int
    suggested_total: int
    extra_cost: int
    promotion: str


def suggest_quantity(item: CartItem, line: LineEvaluation) -> QuantitySuggestion | None:
    if line.record is None:
        return None
    promotion = normalize_promotion(line.record.promotion)
    size = {'1+1': 2, '2+1': 3}.get(promotion)
    if size is None or item.quantity % size == 0:
        return None
    target = item.quantity + size - item.quantity % size
    if target > 10:
        return None
    before = confirmed_cost(line, item.quantity)
    after = confirmed_cost(line, target)
    if before is None or after is None:
        return None
    return QuantitySuggestion(item.item_id, item.quantity, target, before, after, after-before, promotion)
