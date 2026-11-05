"""Compare complete-item purchases at one or two confirmed stores."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, product
from math import isfinite

from core.cart_models import CartItem, StoreEvaluation
from .distance import haversine_km
from .v2_common import confirmed_cost


@dataclass(frozen=True)
class PurchasePlan:
    assignments: dict[str, str]
    total: int
    store_keys: tuple[str, ...]
    store_names: tuple[str, ...]
    distance_km: float | None
    within_budget: bool

    @property
    def store_count(self) -> int:
        return len(self.store_keys)


def _valid_distance(value: float | None) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0


def _coordinates(store: StoreEvaluation, assigned_ids: set[str]) -> tuple[float, float] | None:
    """Use original observations, never user-to-store distances, for a route."""
    coordinates = set()
    for line in store.lines:
        if line.item_id not in assigned_ids or line.record is None:
            continue
        lat, lng = line.record.latitude, line.record.longitude
        if not (isinstance(lat, (int, float)) and not isinstance(lat, bool)
                and isinstance(lng, (int, float)) and not isinstance(lng, bool)
                and isfinite(lat) and isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        coordinates.add((lat, lng))
    return next(iter(coordinates)) if len(coordinates) == 1 else None


def _visit_order(stores: tuple[StoreEvaluation, ...], assignments: dict[str, str],
                 user_lat: float, user_lng: float) -> tuple[tuple[StoreEvaluation, ...], float | None]:
    if len(stores) == 1:
        return stores, stores[0].distance_km
    points = [_coordinates(store, {item_id for item_id, key in assignments.items()
                                   if key == store.store_key}) for store in stores]
    if any(point is None for point in points):
        return stores, None
    first, second = points
    between = haversine_km(*first, *second)
    from_user = [haversine_km(user_lat, user_lng, *point) for point in points]
    if not all(_valid_distance(distance) for distance in [between, *from_user]):
        return stores, None
    # The store pair starts in stable key order, which also breaks equal-route ties.
    if from_user[1] < from_user[0]:
        return (stores[1], stores[0]), from_user[1] + between
    return stores, from_user[0] + between


def _rank(plan: PurchasePlan) -> tuple:
    return (plan.total, plan.store_count,
            plan.distance_km if plan.distance_km is not None else float('inf'),
            plan.store_keys, tuple(sorted(plan.assignments.items())))


def compare_purchase_plans(cart: list[CartItem], stores: list[StoreEvaluation],
                           user_lat: float, user_lng: float, budget: int = 0,
                           limit: int = 3) -> list[PurchasePlan]:
    """Return a one-store baseline and at most ``limit`` exact two-store plans.

    Each item's entire quantity is purchased from one store. Costs are rechecked
    for that quantity, so promotional bundles cannot cross store boundaries.
    Budget marks plans without removing the single-store comparison baseline.
    Pair distance is the shorter straight-line user -> A -> B route, no return.
    Missing original coordinates leave that route unknown.
    """
    if len(cart) > 5 or len(stores) > 10:
        raise ValueError('최대 상품 5종과 매장 10곳까지 비교할 수 있습니다.')
    if len({item.item_id for item in cart}) != len(cart):
        raise ValueError('장바구니 상품 ID는 중복될 수 없습니다.')
    if len({store.store_key for store in stores}) != len(stores):
        raise ValueError('매장 ID는 중복될 수 없습니다.')
    if type(budget) is not int or budget < 0:
        raise ValueError('예산은 0원 이상의 정수여야 합니다.')
    if type(limit) is not int or limit < 0:
        raise ValueError('비교 결과 수는 0 이상의 정수여야 합니다.')
    if not cart or not stores:
        return []

    candidates = []
    costs = {}
    for store in sorted(stores, key=lambda entry: entry.store_key):
        if not _valid_distance(store.distance_km) or store.source not in ('LIVE', 'TEST'):
            continue
        lines = {}
        duplicate_ids = set()
        for line in store.lines:
            if line.item_id in lines:
                duplicate_ids.add(line.item_id)
            lines[line.item_id] = line
        confirmed = {}
        for item in cart:
            line = lines.get(item.item_id)
            if line is None or item.item_id in duplicate_ids:
                continue
            cost = confirmed_cost(line, item.quantity)
            if type(cost) is int and cost >= 0:
                confirmed[item.item_id] = cost
        if confirmed:
            candidates.append(store)
            costs[store.store_key] = confirmed

    def make_plan(selected: tuple[StoreEvaluation, ...], assignments: dict[str, str]) -> PurchasePlan:
        total = sum(costs[key][item_id] for item_id, key in assignments.items())
        ordered, distance = _visit_order(selected, assignments, user_lat, user_lng)
        return PurchasePlan(assignments, total, tuple(store.store_key for store in ordered),
                            tuple(store.store_name for store in ordered), distance,
                            budget == 0 or total <= budget)

    singles = [make_plan((store,), {item.item_id: store.store_key for item in cart})
               for store in candidates if all(item.item_id in costs[store.store_key] for item in cart)]
    pairs = []
    if limit:
        for pair in combinations(candidates, 2):
            choices = [[store.store_key for store in pair if item.item_id in costs[store.store_key]]
                       for item in cart]
            for allocation in product(*choices):
                if len(set(allocation)) == 2:
                    assignments = {item.item_id: key for item, key in zip(cart, allocation)}
                    pairs.append(make_plan(pair, assignments))
    selected = ([min(singles, key=_rank)] if singles else []) + sorted(pairs, key=_rank)[:limit]
    return sorted(selected, key=_rank)
