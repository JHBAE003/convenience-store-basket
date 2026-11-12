"""반대 방향 우회·방문 순서·왕복을 포함하는 총 도보거리 회귀 검사."""
from dataclasses import replace
from itertools import permutations
import json
import math
from pathlib import Path
import unittest

from planner.engine import MODES, Offer, Product, Settings, Store, route_for, solve
from planner.walk_map import ORIGIN, WalkMap, get_network


def opposite_direction_fixture():
    # 서쪽 B는 동쪽 C보다 출발지에 가깝지만 A를 함께 방문하면 크게 돌아간다.
    coords={1:(37.48,126.949),2:(37.48,126.95),
            3:(37.48,126.951),4:(37.48,126.9518)}
    elements=[{'type':'node','id':i,'lat':p[0],'lon':p[1]} for i,p in coords.items()]
    elements.append({'type':'way','id':10,'nodes':[1,2,3,4],
                     'tags':{'highway':'residential','name':'동서 방향 테스트 보행로'}})
    base=WalkMap({'elements':elements},{'bbox':(37.47,126.94,37.49,126.96)})
    stores=(Store('A','CU','동쪽 필수 매장',*coords[3]),
            Store('B','CU','서쪽 저렴한 매장',*coords[1]),
            Store('C','CU','동쪽 같은 방향 매장',*coords[4]))
    net=base.network(((ORIGIN,coords[2]),)+tuple((s.id,(s.lat,s.lng)) for s in stores))
    products=(Product('p','필수 상품','규격 p'),Product('q','선택 상품','규격 q'))
    offers=(Offer('A','p',1000,1,spec='규격 p'),
            Offer('B','q',900,1,spec='규격 q'),Offer('C','q',1100,1,spec='규격 q'))
    settings=Settings(origin=coords[2],brands=('CU',),max_stores=2,
                      distance_weight=3000,visit_weight=0,as_of='2026-11-15')
    return stores,net,products,offers,settings


class TotalWalkingRouteTests(unittest.TestCase):
    def test_opposite_shop_is_nearer_radially_but_longer_as_a_complete_tour(self):
        stores,net,_,_,settings=opposite_direction_fixture()
        self.assertLess(net.distance(ORIGIN,'B'),net.distance(ORIGIN,'C'))
        route_opposite,total_opposite=route_for(stores[:2],settings,
                                               {(a,b):v for a,b,v in net.matrix()})
        route_same,total_same=route_for((stores[0],stores[2]),settings,
                                       {(a,b):v for a,b,v in net.matrix()})
        self.assertGreater(total_opposite,total_same)
        self.assertGreater(total_opposite,net.distance(ORIGIN,'A')+net.distance(ORIGIN,'B'))
        self.assertAlmostEqual(total_same,sum(x.distance_km for x in net.legs(route_same)))
        self.assertAlmostEqual(total_opposite,sum(x.distance_km for x in net.legs(route_opposite)))

    def test_lowest_price_remains_price_first_but_distance_and_balance_avoid_detour(self):
        stores,net,products,offers,settings=opposite_direction_fixture()
        result=solve(products,stores,offers,{'p':1,'q':1},settings,net.matrix())
        cheapest=result.winners[MODES[0]]
        shortest=result.winners[MODES[2]]
        balanced=result.winners[MODES[3]]
        self.assertEqual(set(cheapest.route),{'A','B'})
        self.assertEqual(cheapest.total,1900)
        self.assertEqual(set(shortest.route),{'A','C'})
        self.assertEqual(shortest.total,2100)
        self.assertEqual(set(balanced.route),{'A','C'})
        self.assertLess(balanced.distance_km,cheapest.distance_km)
        self.assertLess(balanced.score,cheapest.score)
        self.assertAlmostEqual(balanced.score,
                               balanced.total+balanced.distance_km*settings.distance_weight)

    def test_route_limit_applies_to_complete_walk_not_each_stores_origin_distance(self):
        stores,net,products,offers,settings=opposite_direction_fixture()
        # B만 보면100m 이내지만 A와 함께 방문한 총 동선은200m를 넘는다.
        self.assertLess(net.distance(ORIGIN,'B'),.1)
        result=solve(products,stores,offers,{'p':1,'q':1},
                     replace(settings,route_limit_km=.2),net.matrix())
        self.assertTrue(result.plans)
        self.assertTrue(all(p.distance_km<=.2 for p in result.plans))
        self.assertEqual(set(result.winners[MODES[0]].route),{'A','C'})
        self.assertFalse(any(set(p.route)=={'A','B'} for p in result.plans))

    def test_three_stop_order_is_reoptimized_when_return_trip_is_required(self):
        stores=tuple(Store(k,'CU',k,37.48,126.95) for k in ('A','B','C'))
        settings=Settings(origin=(37.48,126.95))
        distances={(ORIGIN,'A'):2,(ORIGIN,'B'):1,(ORIGIN,'C'):4,
                   ('A','B'):4,('A','C'):1,('A',ORIGIN):1,
                   ('B','A'):1,('B','C'):5,('B',ORIGIN):2,
                   ('C','A'):1,('C','B'):1,('C',ORIGIN):8}
        self.assertEqual(route_for(stores,settings,distances),(('B','A','C'),3))
        self.assertEqual(route_for(stores,replace(settings,return_home=True),distances),
                         (('A','C','B'),6))

    def test_return_home_adds_actual_final_leg_not_the_last_stores_origin_radius(self):
        stores,net,_,_,settings=opposite_direction_fixture()
        distances={(a,b):v for a,b,v in net.matrix()}
        same_side=(stores[0],stores[2])
        route,total=route_for(same_side,replace(settings,return_home=True),distances)
        manual=sum(leg.distance_km for leg in net.legs(route,True))
        self.assertAlmostEqual(total,manual)
        self.assertEqual(len(net.legs(route,True)),3)
        self.assertGreater(total,route_for(same_side,settings,distances)[1])

    def test_real_osm_three_store_tour_matches_every_directed_leg_and_its_minimum(self):
        root=Path(__file__).resolve().parents[1]
        source=json.loads((root/'data/store_sources.json').read_text(encoding='utf-8'))
        origin=(source['origin']['lat'],source['origin']['lng'])
        rows=source['stores'][:3]
        stores=tuple(Store(r['id'],r['brand'],r['name'],r['lat'],r['lng']) for r in rows)
        points=((ORIGIN,origin),)+tuple((s.id,(s.lat,s.lng)) for s in stores)
        network=get_network(tuple(sorted(points)))
        self.assertFalse(network.issues)
        self.assertEqual(network.base.metadata['nodes'],len(network.base.nodes))
        self.assertEqual(network.base.metadata['ways'],len(network.base.ways))
        self.assertGreater(len(network.base.edges),10000)
        self.assertIsNone(network.base.metadata['source_updated_at'])
        matrix={(a,b):v for a,b,v in network.matrix()}
        for home in (False,True):
            settings=Settings(origin=origin,return_home=home)
            route,total=route_for(stores,settings,matrix)
            self.assertTrue(math.isfinite(total))
            legs=network.legs(route,home)
            self.assertAlmostEqual(total,sum(leg.distance_km for leg in legs),places=9)
            alternatives=[]
            for order in permutations(s.id for s in stores):
                ids=(ORIGIN,)+order+((ORIGIN,) if home else ())
                alternatives.append(sum(network.distance(a,b) for a,b in zip(ids,ids[1:])))
            self.assertAlmostEqual(total,min(alternatives),places=9)
            self.assertEqual(len(legs),len(stores)+int(home))


if __name__=='__main__':unittest.main()
