"""Find confirmed, nonempty baskets while removing as few units as possible."""
from dataclasses import dataclass
from math import isfinite

from core.cart_models import CartItem, StoreEvaluation
from core.services.v2_common import confirmed_cost


@dataclass(frozen=True)
class BudgetPlan:
    store_key: str
    store_name: str
    quantities: dict[str, int]
    total: int
    removed_units: int
    changed_items: int


def optimize_budget(cart: list[CartItem], stores: list[StoreEvaluation], budget: int,
                    locked_ids: set[str] | None = None, limit: int = 3) -> list[BudgetPlan]:
    """Return the best quantity reductions using only confirmed line evidence.

    A quantity is the number received, including free promotion units. Zero is
    permitted only for an unlocked item. An unmodified basket is a valid plan
    for a positive budget; a zero budget means no adjustment was requested.
    """
    if type(budget) is not int or budget < 0:
        raise ValueError('예산은 0원 이상의 정수로 입력하세요.')
    if type(limit) is not int or limit < 0:
        raise ValueError('추천 개수는 0 이상의 정수로 입력하세요.')
    if len(cart) > 5 or len(stores) > 10:
        raise ValueError('예산 조정은 상품 5개, 매장 10곳까지 지원합니다.')
    item_ids = [item.item_id for item in cart]
    if len(set(item_ids)) != len(item_ids):
        raise ValueError('장바구니 상품 식별자는 중복될 수 없습니다.')
    if not cart or budget == 0 or limit == 0:
        return []

    locked_ids = set(locked_ids or ())
    original_units = sum(item.quantity for item in cart)
    signature_order = sorted(range(len(cart)), key=lambda index: item_ids[index])
    # Keep only the requested best results; the worst retained result bounds
    # later branches. The final quantity signature makes all ties reproducible.
    best = []

    def retain(store, quantities, total, changed):
        signature = tuple(quantities[index] for index in signature_order)
        removed = original_units - sum(quantities)
        rank = (removed, changed, total, store.store_key, signature)
        identity = (store.store_key, signature)
        for index, (_, existing_identity, _) in enumerate(best):
            if existing_identity == identity:
                if best[index][0] <= rank:
                    return
                best.pop(index)
                break
        plan = BudgetPlan(store.store_key, store.store_name,
                          dict(zip(item_ids, quantities)), total, removed, changed)
        best.append((rank, identity, plan))
        best.sort(key=lambda entry: entry[0])
        del best[limit:]

    for store in sorted(stores, key=lambda value: (value.store_key, value.store_name)):
        if (store.source not in ('LIVE', 'TEST') or store.distance_km is None
                or not isfinite(store.distance_km) or store.distance_km < 0):
            continue
        lines = {}
        duplicate_ids = set()
        for line in store.lines:
            if line.item_id in lines:
                duplicate_ids.add(line.item_id)
            lines[line.item_id] = line
        choices = []
        for item in cart:
            line = lines.get(item.item_id)
            allowed = []
            quantities = (item.quantity,) if item.item_id in locked_ids else range(item.quantity, -1, -1)
            for quantity in quantities:
                if quantity == 0:
                    allowed.append((0, 0))
                elif line is not None and item.item_id not in duplicate_ids:
                    cost = confirmed_cost(line, quantity)
                    if cost is not None:
                        allowed.append((quantity, cost))
            if not allowed:
                break
            choices.append(allowed)
        if len(choices) != len(cart):
            continue

        size = len(cart)
        max_units = [0] * (size + 1)
        min_changes = [0] * (size + 1)
        min_costs = [0] * (size + 1)
        for index in range(size - 1, -1, -1):
            max_quantity = choices[index][0][0]
            max_units[index] = max_units[index + 1] + max_quantity
            min_changes[index] = min_changes[index + 1] + (max_quantity != cart[index].quantity)
            min_costs[index] = min_costs[index + 1] + min(cost for _, cost in choices[index])
        selected = [0] * size

        def search(index, total, kept_units, changed):
            if total + min_costs[index] > budget or kept_units + max_units[index] == 0:
                return
            if len(best) == limit:
                minimum_removed = original_units - kept_units - max_units[index]
                worst = best[-1][0]
                if (minimum_removed > worst[0]
                        or (minimum_removed == worst[0] and changed + min_changes[index] > worst[1])):
                    return
            if index == size:
                retain(store, selected, total, changed)
                return
            for quantity, cost in choices[index]:
                if total + cost <= budget:
                    selected[index] = quantity
                    search(index + 1, total + cost, kept_units + quantity,
                           changed + (quantity != cart[index].quantity))

        search(0, 0, 0, 0)

    return [entry[2] for entry in best]
