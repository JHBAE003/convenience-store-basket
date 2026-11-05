import unittest
from dataclasses import replace
from itertools import product,permutations
import random

from planner.engine import *
from planner.scenarios import *

def oracle(products,stores,offers,cart,s):
    """Independent small full cross-product oracle; not the production DP."""
    byoffer={(o.store_id,o.product_id):o for o in offers}
    plans=[];pids=sorted(cart)
    # Deliberately enumerate all per-item/store quantities for a tiny fixture.
    per_item=[[v for v in product(range(cart[pid]+1),repeat=len(stores)) if sum(v)==cart[pid]] for pid in pids]
    for assignment in product(*per_item):
        used={i for a in assignment for i,q in enumerate(a) if q}
        if not used or len(used)>s.max_stores:continue
        chosen=[stores[i] for i in sorted(used)]
        if any(st.brand not in s.brands or haversine_km(*s.origin,st.lat,st.lng)>s.radius_km for st in chosen):continue
        total=0;signature=[];valid=True
        for pid,counts in zip(pids,assignment):
            for st,qty in zip(stores,counts):
                if not qty:continue
                o=byoffer.get((st.id,pid))
                if o is None or type(o.stock)is not int or o.stock<qty or type(o.price)is not int or o.price<=0:
                    valid=False;break
                # Separate arithmetic, no production cost/allocations/DP/rank helpers.
                group={'1+1':2,'2+1':3}.get(o.promotion)
                total+=(qty-qty//group if group else qty)*o.price
                signature.append((st.id,pid,qty))
            if not valid:break
        if not valid:continue
        route_candidates=[]
        for order in permutations(chosen):
            points=[s.origin]+[(st.lat,st.lng) for st in order]+([s.origin] if s.return_home else [])
            distance=sum(haversine_km(*a,*b) for a,b in zip(points,points[1:]))
            route_candidates.append((distance,tuple(st.id for st in order)))
        distance,route=min(route_candidates)
        if distance>s.route_limit_km or (s.strict_budget and total>s.budget):continue
        plans.append(dict(total=total,distance=round(distance,9),count=len(used),route=route,
                          signature=tuple(sorted(signature)),score=round(total+distance*s.distance_weight+(len(used)-1)*s.visit_weight,6)))
    winners={}
    for mode in MODES:
        pool=[p for p in plans if (mode!=MODES[1] or p['count']==1) and (mode!=MODES[4] or p['total']<=s.budget)]
        def key(p):
            if mode==MODES[0]:order=(p['total'],p['distance'],p['count'])
            elif mode==MODES[1]:order=(p['total'],p['distance'])
            elif mode in (MODES[2],MODES[4]):order=(p['distance'],p['total'],p['count'])
            else:order=(p['score'],p['count'],p['total'])
            return *order,p['route'],p['signature']
        winners[mode]=min(pool,key=key,default=None)
    return winners

class EngineTests(unittest.TestCase):
    def fixture(self,id,**kw):
        s=get_scenario(id);return solve(PRODUCTS,STORES,s.offers,dict(s.cart),replace(s.settings,**kw))

    def test_eight_scenarios(self):
        for id,total,count in [(1,1500,1),(2,1600,1),(3,3600,2),(4,2300,3),(5,4400,2),(6,2000,1),(7,1400,1)]:
            with self.subTest(id=id):
                p=self.fixture(id).winners[MODES[0]];self.assertEqual((p.total,p.count),(total,count))
        self.assertFalse(self.fixture(8).plans)

    def test_promotion_boundaries(self):
        for promo,expected in [('',[100,200,300,400,500,600]),('1+1',[100,100,200,200,300,300]),('2+1',[100,200,200,300,400,400])]:
            o=Offer('A','milk',100,12,promo)
            self.assertEqual([cost(o,q,'2026-11-15') for q in range(1,7)],expected)

    def test_gifts_consume_stock_and_no_cross_store_bundle(self):
        p=PRODUCTS[0]
        offers=tuple(Offer(st.id,p.id,100,1,'1+1',spec=p.spec) for st in STORES[:2])
        r=solve((p,),STORES[:2],offers,{p.id:2})
        self.assertEqual(r.winners[MODES[0]].total,200)
        self.assertIsNone(r.winners[MODES[1]])
        self.assertEqual(solve((p,),STORES[:1],offers[:1],{p.id:2}).shortages[p.id],1)

    def test_same_item_split_and_three_store_requirement(self):
        p=self.fixture(5).winners[MODES[0]]
        self.assertEqual([(l.store_id,l.quantity) for l in p.lines],[('A',2),('B',3)])
        self.assertFalse(self.fixture(4,max_stores=2).plans)
        partial=self.fixture(4,max_stores=2).partial
        self.assertEqual(sum(l.quantity for l in partial.lines),2)

    def test_three_can_be_cheaper_than_two(self):
        offers=tuple(Offer(s.id,p.id,100 if s.id==('A','B','C')[i] else 1000,2,spec=p.spec)
                     for i,p in enumerate(PRODUCTS) for s in STORES[:3])
        r=solve(PRODUCTS,STORES[:3],offers,{p.id:1 for p in PRODUCTS})
        self.assertEqual((r.winners[MODES[0]].total,r.winners[MODES[0]].count),(300,3))
        self.assertTrue(any(p.count==2 for p in r.plans))

    def test_brands_radius_route_visits(self):
        self.assertFalse(self.fixture(3,brands=('CU',)).plans)
        self.assertFalse(self.fixture(3,radius_km=.2).plans)
        self.assertFalse(self.fixture(3,route_limit_km=.2).plans)
        self.assertFalse(self.fixture(3,max_stores=1).plans)
        self.assertFalse(self.fixture(1,brands=()).plans)

    def test_budget_strict_reference_and_zero(self):
        self.assertIsNotNone(self.fixture(2,budget=100,strict_budget=False).winners[MODES[0]])
        self.assertIsNone(self.fixture(2,budget=100,strict_budget=False).winners[MODES[4]])
        self.assertFalse(self.fixture(2,budget=100,strict_budget=True).plans)
        self.assertFalse(self.fixture(2,budget=0,strict_budget=True).plans)

    def test_budget_failure_explained_and_partial(self):
        r=self.fixture(1,budget=100,strict_budget=True)
        self.assertTrue(any('최소 결제액' in text and '예산' in text for text in r.reasons))
        r=self.fixture(8,budget=5000,strict_budget=False)
        self.assertEqual((r.shortages['milk'],r.partial.total),(3,1800))

    def test_budget_only_partial_and_double_failure_reason(self):
        r=self.fixture(1,budget=1000,strict_budget=True)
        self.assertFalse(r.plans)
        self.assertIsNotNone(r.partial)
        self.assertEqual((r.partial.total,sum(x.quantity for x in r.partial.lines)),(1000,2))
        self.assertTrue(any('예산' in text for text in self.fixture(8).reasons))

    def test_same_brand_different_branches(self):
        p=PRODUCTS[0];stores=tuple(replace(s,brand='CU') for s in STORES[:2])
        offers=tuple(Offer(s.id,p.id,100,1,spec=p.spec) for s in stores)
        r=solve((p,),stores,offers,{p.id:2},Settings(brands=('CU',)))
        self.assertEqual(r.winners[MODES[0]].count,2)

    def test_return_route_and_permutations(self):
        outward=self.fixture(4).winners[MODES[0]]
        back=self.fixture(4,return_home=True).winners[MODES[0]]
        self.assertGreater(back.distance_km,outward.distance_km)
        # The geometric shortest route is not alphabetical A,B,C.
        self.assertEqual(outward.route,('A','C','B'))

    def test_modes_and_balance_weights(self):
        r=self.fixture(7)
        self.assertNotEqual(r.winners[MODES[0]].route,r.winners[MODES[2]].route)
        high=self.fixture(7,distance_weight=100000)
        self.assertEqual(high.winners[MODES[3]].route,('A',))

    def test_fixed_date_expired_unknown_and_future(self):
        o=Offer('A','milk',100,12,'1+1')
        self.assertEqual(cost(o,2,'2026-11-30'),100)
        self.assertEqual(cost(o,2,'2026-12-01'),200)
        self.assertEqual(cost(o,2,'2026-10-31'),200)
        self.assertEqual(cost(replace(o,conditions_verified=False),2,'2026-11-15'),200)
        self.assertEqual(cost(replace(o,start=''),2,'2026-11-15'),200)
        self.assertIn('미확인',promotion_state(replace(o,start=''),'2026-11-15')[1])

    def test_unknown_missing_zero_and_mismatched_spec(self):
        for stock,price,spec in [(None,100,PRODUCTS[0].spec),(0,100,PRODUCTS[0].spec),(2,None,PRODUCTS[0].spec),(2,100,'1L 딸기맛 2입')]:
            r=solve(PRODUCTS,STORES,(Offer('A','milk',price,stock,spec=spec),),{'milk':1})
            self.assertFalse(r.plans)

    def test_determinism_and_stable_ties(self):
        p=PRODUCTS[0];stores=(replace(STORES[0],lat=37.567,lng=126.978),replace(STORES[1],lat=37.567,lng=126.978))
        offers=tuple(Offer(s.id,p.id,100,2,spec=p.spec) for s in stores)
        r=solve((p,),stores,offers,{p.id:1})
        self.assertEqual(r.winners[MODES[0]].route,('A',))
        self.assertEqual(r.winners,solve((p,),tuple(reversed(stores)),tuple(reversed(offers)),{p.id:1}).winners)

    def test_validation(self):
        s=get_scenario(1)
        for cart,setting in [({'milk':13},Settings()),({'milk':True},Settings()),({'unknown':1},Settings()),({'milk':1},Settings(origin=(float('nan'),0))),({'milk':1},Settings(max_stores=4))]:
            with self.assertRaises(ValueError):solve(PRODUCTS,STORES,s.offers,cart,setting)

    def test_random_small_full_enumeration(self):
        rng=random.Random(3000)
        for trial in range(36):
            ps=PRODUCTS[:2];ss=STORES[:3]
            offers=tuple(Offer(s.id,p.id,rng.choice([100,200,300]),rng.randrange(4),rng.choice(['','1+1','2+1']),spec=p.spec) for s in ss for p in ps)
            cart={p.id:rng.randint(1,3) for p in ps}
            setting=Settings(max_stores=rng.randint(1,3),return_home=bool(trial%2),budget=rng.choice([100,600,3000]),strict_budget=bool(trial%3==0),distance_weight=rng.choice([0,100,1000]))
            actual=solve(ps,ss,offers,cart,setting).winners;expected=oracle(ps,ss,offers,cart,setting)
            for mode in MODES:
                with self.subTest(trial=trial,mode=mode):
                    a,b=actual[mode],expected[mode]
                    if b is None:self.assertIsNone(a)
                    else:self.assertEqual((a.total,round(a.distance_km,9),a.route,a.signature),(b['total'],b['distance'],b['route'],b['signature']))

if __name__=='__main__':unittest.main()
