import itertools
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta

from core.cart_models import CartItem, LineEvaluation, StoreEvaluation
from core.models import StoreProduct
from core.services.budget_optimizer import BudgetPlan, optimize_budget
from core.services.cart_pricing import evaluate_line


def make_store(cart, key='CU|A', prices=None, promotions=None, stocks=None, source='TEST'):
    prices = prices or [100] * len(cart)
    promotions = promotions or [''] * len(cart)
    stocks = stocks or [10] * len(cart)
    lines = []
    observed = datetime.now().astimezone().isoformat()
    for index, item in enumerate(cart):
        row = StoreProduct('CU', key, '주소', item.label, price=prices[index],
                           promotion=promotions[index], stock=stocks[index],
                           store_code=key, data_source=source,
                           stock_updated_at=observed, price_updated_at=observed,
                           promotion_updated_at=observed,
                           price_data_status=source, stock_data_status=source,
                           promotion_data_status=source)
        lines.append(evaluate_line(item, row))
    total = sum(line.payable for line in lines) if all(line.payable is not None for line in lines) else None
    return StoreEvaluation(key, 'CU', key, 0.5, lines, total,
                           sum(line.payable or 0 for line in lines), total,
                           all(line.stock_state == 'ENOUGH' for line in lines), source, [])


def exhaustive_reference(cart, stores, budget, locked_ids, limit):
    """Independent small-input oracle: explicit bundle arithmetic and all vectors."""
    candidates = []
    ids = sorted(item.item_id for item in cart)
    for store in stores:
        lines = {line.item_id: line for line in store.lines}
        options = [(item.quantity,) if item.item_id in locked_ids else range(item.quantity + 1) for item in cart]
        for vector in itertools.product(*options):
            if not any(vector):
                continue
            total = 0
            for item, quantity in zip(cart, vector):
                row = lines[item.item_id].record
                if quantity > row.stock:
                    break
                if row.promotion == '1+1':
                    paid = quantity // 2 + quantity % 2
                elif row.promotion == '2+1':
                    paid = quantity // 3 * 2 + quantity % 3
                else:
                    paid = quantity
                total += paid * row.price
            else:
                if total <= budget:
                    quantities = dict(zip((item.item_id for item in cart), vector))
                    plan = BudgetPlan(store.store_key, store.store_name, quantities, total,
                                      sum(item.quantity - quantity for item, quantity in zip(cart, vector)),
                                      sum(item.quantity != quantity for item, quantity in zip(cart, vector)))
                    rank = (plan.removed_units, plan.changed_items, plan.total,
                            plan.store_key, tuple(quantities[item_id] for item_id in ids))
                    candidates.append((rank, plan))
    return [plan for _, plan in sorted(candidates, key=lambda entry: entry[0])[:limit]]


class BudgetOptimizerTests(unittest.TestCase):
    def test_matches_independent_exhaustive_small_reference(self):
        rng = random.Random(284)
        for case in range(60):
            cart = [CartItem(f'i{index}', f'상품 {index}', rng.randint(1, 3))
                    for index in range(rng.randint(1, 4))]
            stores = [make_store(cart, f'CU|{store}',
                                 [rng.randrange(1, 8) * 100 for _ in cart],
                                 [rng.choice(['', '1+1', '2+1']) for _ in cart],
                                 [rng.randint(1, 4) for _ in cart])
                      for store in range(rng.randint(1, 3))]
            locked = {item.item_id for item in cart if rng.random() < 0.3}
            budget = rng.randint(1, 18) * 100
            limit = rng.randint(1, 7)
            with self.subTest(case=case):
                self.assertEqual(optimize_budget(cart, stores, budget, locked, limit),
                                 exhaustive_reference(cart, stores, budget, locked, limit))

    def test_free_unit_boundary_keeps_the_free_unit(self):
        cart = [CartItem('drink', '음료', 3)]
        plans = optimize_budget(cart, [make_store(cart, prices=[1000], promotions=['1+1'])], 1000)
        self.assertEqual([(plan.quantities, plan.total) for plan in plans],
                         [({'drink': 2}, 1000), ({'drink': 1}, 1000)])
        cart = [CartItem('drink', '음료', 4)]
        plans = optimize_budget(cart, [make_store(cart, prices=[1000], promotions=['2+1'])], 2000)
        self.assertEqual(plans[0].quantities, {'drink': 3})
        self.assertEqual(plans[0].removed_units, 1)

    def test_locked_quantities_are_exact_and_impossible_lock_has_no_solution(self):
        cart = [CartItem('a', 'A', 2), CartItem('b', 'B', 2)]
        store = make_store(cart, prices=[300, 100])
        plans = optimize_budget(cart, [store], 700, {'a'}, limit=10)
        self.assertEqual([plan.quantities for plan in plans], [{'a': 2, 'b': 1}, {'a': 2, 'b': 0}])
        self.assertEqual(optimize_budget(cart, [store], 500, {'a'}), [])
        short = make_store(cart, prices=[300, 100], stocks=[1, 10])
        self.assertEqual(optimize_budget(cart, [short], 1000, {'a'}), [])

    def test_optional_unknown_or_missing_row_can_only_be_removed(self):
        cart = [CartItem('a', 'A', 2), CartItem('b', 'B', 2)]
        store = make_store(cart)
        for missing in [False, True]:
            candidate = replace(store, lines=[store.lines[0]] if missing else
                                [store.lines[0], evaluate_line(cart[1], None)])
            with self.subTest(missing=missing):
                plans = optimize_budget(cart, [candidate], 1000, limit=10)
                self.assertTrue(plans)
                self.assertTrue(all(plan.quantities['b'] == 0 for plan in plans))
                self.assertTrue(all(set(plan.quantities) == {'a', 'b'} for plan in plans))
                self.assertEqual(optimize_budget(cart, [candidate], 1000, {'b'}), [])

    def test_stale_conflicting_and_unconfirmed_evidence_is_not_purchasable(self):
        cart = [CartItem('a', 'A', 2)]
        store = make_store(cart, source='LIVE')
        row = store.lines[0].record
        self.assertTrue(optimize_budget(cart, [store], 1000))
        old = (datetime.now().astimezone() - timedelta(minutes=10)).isoformat()
        bad_rows = [replace(row, stock_updated_at=old), replace(row, price_updated_at=old),
                    replace(row, promotion_updated_at=old), replace(row, stock_updated_at=''),
                    replace(row, price_updated_at=''), replace(row, promotion_updated_at=''),
                    replace(row, stock_known=False),
                    replace(row, stock_quantity_exact=False), replace(row, price=0),
                    replace(row, promotion_known=False), replace(row, promotion='3+1'),
                    replace(row, price_data_status='CACHE'), replace(row, stock_data_status='CACHE'),
                    replace(row, promotion_data_status='ERROR'), replace(row, data_source='PARTIAL')]
        for field in ['price_data_status', 'stock_data_status', 'promotion_data_status']:
            bad_rows.extend(replace(row, **{field: status}) for status in ['UNKNOWN', 'PARTIAL', 'ERROR', 'CACHE'])
        for bad_row in bad_rows:
            with self.subTest(row=bad_row):
                candidate = replace(store, lines=[evaluate_line(cart[0], bad_row)])
                self.assertEqual(optimize_budget(cart, [candidate], 1000), [])
        conflict = LineEvaluation('a', 2, None, None, 'UNKNOWN', 'UNKNOWN', '충돌', row)
        self.assertEqual(optimize_budget(cart, [replace(store, lines=[conflict])], 1000), [])
        self.assertEqual(optimize_budget(cart, [replace(store, lines=[store.lines[0], store.lines[0]])], 1000), [])

    def test_source_and_distance_are_required_even_if_cached_total_looks_valid(self):
        cart = [CartItem('a', 'A', 2)]
        store = make_store(cart)
        for source in ['CACHE', 'PARTIAL', 'ERROR', 'UNKNOWN']:
            with self.subTest(source=source):
                self.assertEqual(optimize_budget(cart, [replace(store, source=source)], 1000), [])
        for distance in [None, float('nan'), float('inf'), -1]:
            with self.subTest(distance=distance):
                self.assertEqual(optimize_budget(cart, [replace(store, distance_km=distance)], 1000), [])
        self.assertTrue(optimize_budget(cart, [replace(store, distance_km=0)], 1000))

    def test_unchanged_feasible_basket_ranks_first_and_zero_means_unlimited(self):
        cart = [CartItem('a', 'A', 2), CartItem('b', 'B', 1)]
        store = make_store(cart)
        plan = optimize_budget(cart, [store], 300)[0]
        self.assertEqual(plan.quantities, {'a': 2, 'b': 1})
        self.assertEqual((plan.total, plan.removed_units, plan.changed_items), (300, 0, 0))
        self.assertEqual(optimize_budget(cart, [store], 0), [])
        self.assertEqual(optimize_budget([], [store], 100), [])
        self.assertEqual(optimize_budget(cart, [], 100), [])

    def test_ranking_prefers_fewer_changes_before_lower_cost(self):
        cart = [CartItem('a', 'A', 2), CartItem('b', 'B', 2)]
        plans = optimize_budget(cart, [make_store(cart, prices=[100, 300])], 400, limit=10)
        # Both remove two units. Removing one item preserves the other item intact.
        self.assertEqual(plans[0].quantities, {'a': 2, 'b': 0})
        self.assertEqual(plans[1].quantities, {'a': 1, 'b': 1})
        self.assertEqual([plan.removed_units for plan in plans[:2]], [2, 2])
        self.assertEqual([plan.changed_items for plan in plans[:2]], [1, 2])

    def test_deterministic_ties_and_unique_store_quantity_vectors(self):
        cart = [CartItem('b', 'B', 1), CartItem('a', 'A', 1)]
        stores = [make_store(cart, 'CU|B'), make_store(cart, 'CU|A')]
        first = optimize_budget(cart, stores, 100, limit=10)
        second = optimize_budget(list(reversed(cart)), list(reversed(stores)), 100, limit=10)
        self.assertEqual(first, second)
        self.assertEqual([(plan.store_key, plan.quantities['a']) for plan in first],
                         [('CU|A', 0), ('CU|A', 1), ('CU|B', 0), ('CU|B', 1)])
        duplicated = optimize_budget(cart, [stores[0], stores[0]], 100, limit=10)
        self.assertEqual(len(duplicated), 2)
        self.assertEqual(len(optimize_budget(cart, stores, 100, limit=1)), 1)

    def test_never_returns_an_empty_basket_or_exceeds_quantity_and_search_bounds(self):
        cart = [CartItem('a', 'A', 10)]
        self.assertEqual(optimize_budget(cart, [make_store(cart)], 99), [])
        plans = optimize_budget(cart, [make_store(cart, stocks=[2])], 1000, limit=20)
        self.assertEqual([plan.quantities['a'] for plan in plans], [2, 1])
        self.assertTrue(all(0 < plan.quantities['a'] <= 10 for plan in plans))
        self.assertEqual(optimize_budget(cart, [make_store(cart)], 1000, limit=0), [])
        for budget in [-1, True, 1.5]:
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                optimize_budget(cart, [], budget)
        with self.assertRaises(ValueError):
            optimize_budget([CartItem(str(index), 'A', 1) for index in range(6)], [], 100)
        with self.assertRaises(ValueError):
            optimize_budget(cart, [make_store(cart)] * 11, 100)
        with self.assertRaises(ValueError):
            optimize_budget([cart[0], cart[0]], [], 100)


if __name__ == '__main__':
    unittest.main()
