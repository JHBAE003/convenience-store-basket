from dataclasses import replace
import math
import unittest
import xml.etree.ElementTree as ET
from planner.engine import Product,Store,Offer,Settings,solve,route_for,partial_budget_examples,haversine_km
from planner.walk_map import WalkMap,load_map,get_network,ORIGIN,walkable,walking_svg,route_gpx
from planner.test_data import TestDataset


def fixture(tags=None):
    coords={1:(37.48,126.95),2:(37.481,126.95),3:(37.481,126.951),4:(37.48,126.951)}
    elements=[{'type':'node','id':key,'lat':lat,'lon':lng} for key,(lat,lng) in coords.items()]
    elements.append({'type':'way','id':10,'nodes':[1,2,3,4],'tags':{'highway':'residential',**(tags or {})}})
    return WalkMap({'elements':elements},{'bbox':(37.47,126.94,37.49,126.96)})


class WalkMapTests(unittest.TestCase):
    def test_path_follows_detour_not_straight_line(self):
        base=fixture();net=base.network(((ORIGIN,base.nodes[1]),('B',base.nodes[4])))
        leg=net.leg(ORIGIN,'B')
        self.assertGreater(leg.distance_km,haversine_km(*base.nodes[1],*base.nodes[4])*3)
        self.assertEqual(leg.points[0],base.nodes[1]);self.assertEqual(leg.points[-1],base.nodes[4])
        self.assertGreaterEqual(len(leg.points),4)

    def test_vehicle_oneway_does_not_forbid_walking_back(self):
        base=fixture({'oneway':'yes'});net=base.network((('a',base.nodes[1]),('b',base.nodes[4])))
        self.assertAlmostEqual(net.distance('a','b'),net.distance('b','a'))

    def test_foot_oneway_and_directional_restrictions_are_respected(self):
        for tag in ({'oneway:foot':'yes'},{'foot:backward':'no'}):
            base=fixture(tag);net=base.network((('a',base.nodes[1]),('b',base.nodes[4])))
            self.assertTrue(math.isfinite(net.distance('a','b')));self.assertTrue(math.isinf(net.distance('b','a')))
        base=fixture({'oneway:foot':'-1'});net=base.network((('a',base.nodes[1]),('b',base.nodes[4])))
        self.assertTrue(math.isinf(net.distance('a','b')));self.assertTrue(math.isfinite(net.distance('b','a')))

    def test_access_area_and_construction_filters(self):
        for tags in ({'highway':'motorway'},{'highway':'residential','access':'private'},
                     {'highway':'service','access':'permit'},{'highway':'primary','foot':'use_sidepath'},
                     {'highway':'footway','foot':'no'},{'highway':'pedestrian','area':'yes'},
                     {'highway':'residential','construction':'yes'},{'highway':'cycleway'}):
            self.assertFalse(walkable(tags))
        self.assertTrue(walkable({'highway':'steps'}))
        self.assertTrue(walkable({'highway':'service','access':'private','foot':'yes'}))

    def test_node_access_is_not_bypassed(self):
        base=fixture()
        data={'elements':[{'type':'node','id':i,'lat':p[0],'lon':p[1],'tags':{'access':'private'} if i==2 else {}} for i,p in base.nodes.items()]+
                         [{'type':'way','id':10,'nodes':[1,2,3,4],'tags':{'highway':'residential'}}]}
        limited=WalkMap(data,base.metadata)
        self.assertNotIn((1,2),limited.edges);self.assertNotIn((2,3),limited.edges)

    def test_snaps_to_edge_instead_of_distant_vertex(self):
        base=fixture();point=(37.4805,126.95)
        snap=base.snap(point)
        self.assertLess(snap.distance_km,1e-9)
        self.assertAlmostEqual(snap.fraction,.5,places=6)
        net=base.network(((ORIGIN,point),('B',base.nodes[1])))
        self.assertAlmostEqual(net.distance(ORIGIN,'B'),haversine_km(*point,*base.nodes[1]),places=7)

    def test_outside_and_distant_points_do_not_get_fake_routes(self):
        base=fixture()
        with self.assertRaises(ValueError):base.snap((37.50,126.95))
        with self.assertRaises(ValueError):base.snap((37.485,126.955))
        net=base.network(((ORIGIN,(37.50,126.95)),('B',base.nodes[1])))
        self.assertIn(ORIGIN,net.issues);self.assertTrue(math.isinf(net.distance(ORIGIN,'B')))
        with self.assertRaises(ValueError):net.leg(ORIGIN,'B')

    def test_coincident_snaps_share_same_road_location(self):
        for tags in ({},{'oneway:foot':'yes'}):
            base=fixture(tags)
            for point in (base.nodes[1],(37.4805,126.95),(37.4805,126.95005)):
                net=base.network(((ORIGIN,point),('A',point),('B',point)))
                for a in (ORIGIN,'A','B'):
                    for b in (ORIGIN,'A','B'):
                        self.assertAlmostEqual(net.distance(a,b),0,places=8)
                        self.assertLess(len(net.leg(a,b).points),6)
                        self.assertEqual(net.leg(a,b).approaches,())

    def test_distinct_locations_with_same_snap_have_no_road_detour(self):
        for tags in ({},{'oneway:foot':'yes'},{'oneway:foot':'-1'}):
            base=fixture(tags)
            for lat in (37.48,37.4805):
                net=base.network(((ORIGIN,(lat,126.95)),('A',(lat,126.95005))))
                expected=sum(s.distance_km for s in net.snaps.values())
                self.assertGreater(expected,0)
                self.assertAlmostEqual(net.distance(ORIGIN,'A'),expected,places=8)
                self.assertAlmostEqual(net.distance('A',ORIGIN),expected,places=8)
                self.assertTrue(all(p==net.snaps[ORIGIN].road_point for p in net.leg(ORIGIN,'A').points))

    def test_real_download_and_all_24_stores_are_connected(self):
        base=load_map();self.assertEqual(base.metadata['ways'],len(base.ways))
        self.assertEqual(base.metadata['nodes'],len(base.nodes));self.assertIsNone(base.metadata['source_updated_at'])
        self.assertGreater(len(base.edges),10000)
        points=((ORIGIN,(37.48121,126.9527)),)+tuple((s['id'],(s['lat'],s['lng'])) for s in TestDataset().stores())
        net=get_network(tuple(sorted(points)));self.assertFalse(net.issues)
        self.assertEqual(len(net.matrix()),600)
        self.assertTrue(all(math.isfinite(d) and d>=0 for a,b,d in net.matrix()))
        self.assertLess(max(s.distance_km for s in net.snaps.values()),.03)

    def test_svg_and_gpx_match_route_geometry_and_return(self):
        base=fixture();net=base.network(((ORIGIN,base.nodes[1]),('B',base.nodes[4])))
        legs=net.legs(('B',),True);self.assertEqual(len(legs),2)
        self.assertAlmostEqual(sum(l.distance_km for l in legs),2*net.distance(ORIGIN,'B'))
        svg=walking_svg(net,('B',),True);ET.fromstring(svg)
        self.assertIn('OpenStreetMap',svg);self.assertIn('stroke-dasharray',svg)
        gpx=ET.fromstring(route_gpx(net,('B',),True))
        ns={'g':'http://www.topografix.com/GPX/1/1'}
        segs=gpx.findall('g:trk/g:trkseg',ns);self.assertEqual(len(segs),2)
        self.assertEqual(len(segs[0]),len(legs[0].points))


class RouteMatrixTests(unittest.TestCase):
    def setUp(self):
        self.stores=(Store('A','CU','A',37.48,126.95),Store('B','CU','B',37.481,126.951))
        self.settings=Settings(origin=(37.48,126.95),brands=('CU',),route_limit_km=30,as_of='2026-11-15')
        self.matrix=((ORIGIN,'A',10),(ORIGIN,'B',1),('A','B',10),('B','A',1),('A',ORIGIN,1),('B',ORIGIN,10))

    def test_network_distances_choose_different_order_and_return(self):
        distances={(a,b):v for a,b,v in self.matrix}
        self.assertEqual(route_for(self.stores,self.settings,distances),(('B','A'),2))
        self.assertEqual(route_for(self.stores,replace(self.settings,return_home=True),distances),(('B','A'),3))

    def test_solver_uses_network_for_ranking_and_limit(self):
        products=(Product('p','p','p'),Product('q','q','q'))
        offers=(Offer('A','p',100,1,spec='p'),Offer('B','q',100,1,spec='q'))
        result=solve(products,self.stores,offers,{'p':1,'q':1},replace(self.settings,route_limit_km=2),self.matrix)
        chosen=result.winners['최단 이동'];self.assertEqual(chosen.route,('B','A'));self.assertEqual(chosen.distance_km,2)
        result=solve(products,self.stores,offers,{'p':1,'q':1},replace(self.settings,route_limit_km=1),self.matrix)
        self.assertFalse(result.plans)
        self.assertFalse(solve(products,self.stores,offers,{'p':1,'q':1},self.settings,()).plans)

    def test_partial_budget_examples_do_not_revert_to_straight_lines(self):
        offers={('A','p'):Offer('A','p',100,2,spec='p')}
        settings=replace(self.settings,budget=100,strict_budget=True,route_limit_km=2)
        self.assertTrue(partial_budget_examples(self.stores,offers,{'p':2},settings))
        self.assertFalse(partial_budget_examples(self.stores,offers,{'p':2},settings,{}))

    def test_bad_matrix_values_are_rejected(self):
        for matrix in (((ORIGIN,'A',-1),),((ORIGIN,'A',float('nan')),),((ORIGIN,'A',True),),self.matrix+self.matrix):
            with self.assertRaises(ValueError):solve((),self.stores,(),{},self.settings,matrix)
