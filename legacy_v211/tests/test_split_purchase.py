import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from itertools import product

from core.cart_models import CartItem
from core.models import StoreProduct
from core.services.basket import evaluate_stores
from core.services.distance import haversine_km
from core.services.split_purchase import compare_purchase_plans


class SplitPurchaseTests(unittest.TestCase):
    def setUp(self):
        self.user = (37.0, 127.0)
        self.cart = [CartItem('drink', '음료', 2), CartItem('snack', '과자', 1)]

    def row(self, store, item, price=1000, stock=10, promotion='', **changes):
        return StoreProduct('CU', store, f'{store} 주소', item, price=price,
                            stock=stock, promotion=promotion, store_code=store,
                            latitude=37.001, longitude=127.0, **changes)

    def evaluate(self, rows, cart=None):
        cart = self.cart if cart is None else cart
        by_item = {item.item_id: [row for row in rows if row.product_name == item.item_id]
                   for item in cart}
        return evaluate_stores(cart, by_item, *self.user, radius_km=1000)

    def compare(self, rows, cart=None, **kwargs):
        cart = self.cart if cart is None else cart
        return compare_purchase_plans(cart, self.evaluate(rows, cart), *self.user, **kwargs)

    def test_complementary_stock_covers_cart_without_one_store(self):
        rows = [self.row('A', 'drink'), self.row('A', 'snack', stock=0),
                self.row('B', 'drink', stock=1), self.row('B', 'snack', price=700)]
        plans = self.compare(rows)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].assignments, {'drink': 'CU|A', 'snack': 'CU|B'})
        self.assertEqual(plans[0].total, 2700)
        self.assertEqual(plans[0].store_count, 2)
        with self.assertRaises(FrozenInstanceError):
            plans[0].total = 1

    def test_cheaper_single_and_fewer_stops_win_equal_cost(self):
        rows = [self.row('A', 'drink', price=500), self.row('A', 'snack', price=500),
                self.row('B', 'drink', price=1000), self.row('B', 'snack', price=500)]
        plans = self.compare(rows)
        self.assertEqual(plans[0].store_keys, ('CU|A',))
        self.assertEqual(plans[0].total, 1500)
        self.assertEqual(plans[1].total, 1500)
        self.assertEqual(plans[1].store_count, 2)
        self.assertTrue(all(plan.total >= plans[0].total for plan in plans))

    def test_best_single_is_kept_alongside_cheaper_pairs(self):
        rows = [self.row('A', 'drink', price=300), self.row('A', 'snack', price=2000),
                self.row('B', 'drink', price=2000), self.row('B', 'snack', price=300)]
        plans = self.compare(rows)
        self.assertEqual(plans[0].total, 900)
        singles = [plan for plan in plans if plan.store_count == 1]
        self.assertEqual(len(singles), 1)
        self.assertEqual(singles[0].total, 2600)

    def test_promotions_are_calculated_for_each_whole_item(self):
        cart = [CartItem('drink', '음료', 3), CartItem('snack', '과자', 3)]
        rows = [self.row('A', 'drink', price=900, promotion='1+1'),
                self.row('B', 'snack', price=600, promotion='2+1')]
        plan = self.compare(rows, cart)[0]
        self.assertEqual(plan.total, 2 * 900 + 2 * 600)
        self.assertEqual(plan.assignments, {'drink': 'CU|A', 'snack': 'CU|B'})

    def test_units_of_one_item_cannot_be_split_across_stores(self):
        cart = [CartItem('drink', '음료', 3), CartItem('snack', '과자', 1)]
        rows = [self.row('A', 'drink', stock=2, promotion='1+1'), self.row('A', 'snack'),
                self.row('B', 'drink', stock=1, promotion='1+1'), self.row('B', 'snack')]
        self.assertEqual(self.compare(rows, cart), [])

    def test_route_is_shorter_open_visit_order_from_original_coordinates(self):
        rows = [replace(self.row('A', 'drink'), latitude=37.03),
                replace(self.row('B', 'snack'), latitude=37.01)]
        stores = self.evaluate(rows)
        # These are user-to-store summaries, not pair-route legs.
        stores = [replace(store, distance_km=123.0) for store in stores]
        plan = compare_purchase_plans(self.cart, stores, *self.user)[0]
        self.assertEqual(plan.store_keys, ('CU|B', 'CU|A'))
        self.assertEqual(plan.store_names, ('B', 'A'))
        expected = haversine_km(*self.user, 37.01, 127.0) + haversine_km(37.01, 127.0, 37.03, 127.0)
        self.assertAlmostEqual(plan.distance_km, expected)

    def test_missing_original_coordinates_do_not_add_user_to_store_distances(self):
        stores = self.evaluate([self.row('A', 'drink'), self.row('B', 'snack')])
        for store in stores:
            for line in store.lines:
                if line.record:
                    line.record = replace(line.record, latitude=None, longitude=None)
        plan = compare_purchase_plans(self.cart, stores, *self.user)[0]
        self.assertIsNone(plan.distance_km)

    def test_unknown_store_location_is_excluded(self):
        stores = self.evaluate([self.row('A', 'drink'), self.row('B', 'snack')])
        for bad_distance in (None, -1.0, float('nan'), float('inf')):
            with self.subTest(distance=bad_distance):
                unknown = [replace(store, distance_km=bad_distance) if store.store_key == 'CU|A'
                           else store for store in stores]
                self.assertEqual(compare_purchase_plans(self.cart, unknown, *self.user), [])

    def test_cost_is_rechecked_instead_of_trusting_saved_totals(self):
        stores = self.evaluate([self.row('A', 'drink'), self.row('B', 'snack')])
        for store in stores:
            store.total = 1
            for line in store.lines:
                if line.record:
                    line.payable = 1
        self.assertEqual(compare_purchase_plans(self.cart, stores, *self.user)[0].total, 3000)

    def test_cached_unknown_and_unsafe_observations_are_rejected(self):
        changes = [dict(data_source='CACHE'), dict(data_source='PARTIAL'), dict(data_source='ERROR'),
                   dict(stock_known=False), dict(stock_quantity_exact=False), dict(price=0),
                   dict(promotion_known=False), dict(promotion='3+1'), dict(stock_data_status='CACHE'),
                   dict(price_data_status='CACHE'), dict(promotion_data_status='ERROR')]
        for change in changes:
            with self.subTest(change=change):
                rows = [replace(self.row('A', 'drink'), **change), self.row('B', 'snack')]
                self.assertEqual(self.compare(rows), [])
        stores = self.evaluate([self.row('A', 'drink'), self.row('B', 'snack')])
        stores[0].source = 'PARTIAL'
        self.assertEqual(compare_purchase_plans(self.cart, stores, *self.user), [])

    def test_stale_live_and_conflicting_evidence_are_rejected(self):
        old = (datetime.now().astimezone() - timedelta(minutes=10)).isoformat()
        stale = replace(self.row('A', 'drink'), data_source='LIVE', stock_updated_at=old)
        self.assertEqual(self.compare([stale, self.row('B', 'snack')]), [])
        recent = datetime.now().astimezone().isoformat()
        fresh = replace(stale, stock_updated_at=recent, price_updated_at=recent,
                        promotion_updated_at=recent, stock_data_status='LIVE',
                        price_data_status='LIVE', promotion_data_status='LIVE')
        stores = self.evaluate([fresh, self.row('B', 'snack')])
        self.assertEqual(len(compare_purchase_plans(self.cart, stores, *self.user)), 1)
        for line in stores[0].lines:
            if line.record:
                line.record = replace(line.record, stock_updated_at=old)
        self.assertEqual(compare_purchase_plans(self.cart, stores, *self.user), [])
        original = self.row('A', 'drink')
        self.assertEqual(self.compare([original, replace(original, price=1200), self.row('B', 'snack')]), [])

    def test_budget_marks_baseline_and_pairs_without_hiding_them(self):
        rows = [self.row('A', 'drink', price=300), self.row('A', 'snack', price=2000),
                self.row('B', 'drink', price=2000), self.row('B', 'snack', price=300)]
        plans = self.compare(rows, budget=900)
        self.assertTrue(plans[0].within_budget)
        self.assertFalse(next(plan for plan in plans if plan.store_count == 1).within_budget)
        self.assertTrue(all(plan.within_budget for plan in self.compare(rows, budget=0)))
        self.assertTrue(all(not plan.within_budget for plan in self.compare(rows, budget=1)))

    def test_ties_are_stable_and_known_shorter_routes_rank_first(self):
        rows = [self.row(store, item, stock=10 if (store == 'A') == (item == 'drink') else 0)
                for store in ('A', 'B', 'C') for item in ('drink', 'snack')]
        # A supplies drinks; B/C supply snacks. C is closer to the user.
        rows = [replace(row, latitude={'A': 37.01, 'B': 37.02, 'C': 37.005}[row.store_code])
                for row in rows]
        plans = self.compare(rows)
        self.assertEqual(plans[0].assignments['snack'], 'CU|C')
        same = [replace(row, latitude=37.01) for row in rows]
        forward = self.compare(same)
        backward = self.compare(list(reversed(same)))
        self.assertEqual(forward, backward)
        self.assertEqual(forward[0].store_keys, ('CU|A', 'CU|B'))
        stores = self.evaluate(same)
        for store in stores:
            if store.store_key == 'CU|B':
                for line in store.lines:
                    if line.record:
                        line.record = replace(line.record, latitude=None, longitude=None)
        self.assertEqual(compare_purchase_plans(self.cart, stores, *self.user)[0].assignments['snack'], 'CU|C')

    def test_small_independent_assignment_reference(self):
        cart = [CartItem('a', 'A', 2), CartItem('b', 'B', 3), CartItem('c', 'C', 1)]
        prices = {'A': [700, 200, 900], 'B': [300, 600, 500], 'C': [500, 400, 300]}
        promotions = {'A': ['', '2+1', ''], 'B': ['1+1', '', ''], 'C': ['', '1+1', '']}
        stocks = {'A': [3, 3, 0], 'B': [2, 3, 2], 'C': [1, 3, 1]}
        rows = [self.row(store, item.item_id, price=prices[store][index],
                         promotion=promotions[store][index], stock=stocks[store][index])
                for store in prices for index, item in enumerate(cart)]
        reference = []
        for allocation in product(prices, repeat=len(cart)):
            used = set(allocation)
            if len(used) > 2:
                continue
            total = 0
            for index, (item, store) in enumerate(zip(cart, allocation)):
                if stocks[store][index] < item.quantity:
                    break
                quantity, promo = item.quantity, promotions[store][index]
                paid = (quantity // 2 + quantity % 2 if promo == '1+1' else
                        quantity // 3 * 2 + quantity % 3 if promo == '2+1' else quantity)
                total += paid * prices[store][index]
            else:
                assignment = tuple((item.item_id, f'CU|{store}') for item, store in zip(cart, allocation))
                reference.append((total, len(used), tuple(f'CU|{key}' for key in sorted(used)), assignment))
        singles = sorted(entry for entry in reference if entry[1] == 1)[:1]
        pairs = sorted(entry for entry in reference if entry[1] == 2)[:3]
        expected = sorted(singles + pairs)
        actual = self.compare(rows, cart)
        self.assertEqual([(plan.total, plan.store_count, plan.store_keys, tuple(sorted(plan.assignments.items())))
                          for plan in actual], expected)

    def test_empty_limit_zero_and_input_limits(self):
        rows = [self.row('A', 'drink'), self.row('A', 'snack'), self.row('B', 'drink'), self.row('B', 'snack')]
        stores = self.evaluate(rows)
        self.assertEqual(compare_purchase_plans([], stores, *self.user), [])
        self.assertEqual(compare_purchase_plans(self.cart, [], *self.user), [])
        self.assertEqual([plan.store_count for plan in self.compare(rows, limit=0)], [1])
        self.assertEqual(len([plan for plan in self.compare(rows, limit=1) if plan.store_count == 2]), 1)
        with self.assertRaises(ValueError):
            compare_purchase_plans([CartItem(str(index), '상품', 1) for index in range(6)], stores, *self.user)
        with self.assertRaises(ValueError):
            compare_purchase_plans(self.cart, [replace(stores[0], store_key=str(index)) for index in range(11)], *self.user)
        for options in (dict(budget=-1), dict(limit=-1), dict(limit=True)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                compare_purchase_plans(self.cart, stores, *self.user, **options)


if __name__ == '__main__':
    unittest.main()
