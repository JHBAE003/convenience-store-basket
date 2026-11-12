"""지도 문자열 재사용의 경로 격리, 상한, 실패 복구와 동시 요청 검사."""
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from planner.walk_map import ORIGIN, RENDER_CACHE_SIZE, WalkMap, walking_svg, route_gpx


def network(offset=0):
    coords={1:(37.48,126.95+offset),2:(37.481,126.95+offset),
            3:(37.481,126.951+offset),4:(37.48,126.951+offset)}
    elements=[{'type':'node','id':key,'lat':lat,'lon':lng} for key,(lat,lng) in coords.items()]
    elements.append({'type':'way','id':10,'nodes':[1,2,3,4],
                     'tags':{'highway':'residential','name':'테스트 도로'}})
    base=WalkMap({'elements':elements},{'bbox':(37.47,126.94,37.49,126.97)})
    return base.network(((ORIGIN,coords[1]),('A',coords[2]),('B',coords[3]),('C',coords[4])))


class MapRenderCacheTests(unittest.TestCase):
    def test_repeat_reuses_completed_render_for_each_format(self):
        net=network()
        with patch.object(net,'legs',wraps=net.legs) as legs:
            svg=walking_svg(net,('A','B'));gpx=route_gpx(net,('A','B'))
            self.assertEqual(legs.call_count,2)
            for _ in range(5):
                self.assertEqual(svg,walking_svg(net,['A','B']))
                self.assertEqual(gpx,route_gpx(net,['A','B']))
            self.assertEqual(legs.call_count,2)
        ET.fromstring(svg);ET.fromstring(gpx)

    def test_route_order_return_flag_and_coordinates_are_isolated(self):
        net=network()
        exports=[route_gpx(net,route,home) for route in (('A','B'),('B','A')) for home in (False,True)]
        self.assertEqual(len(set(exports)),4)
        other=network(.002)
        self.assertNotEqual(route_gpx(net,('A','B')),route_gpx(other,('A','B')))
        ns={'g':'http://www.topografix.com/GPX/1/1'}
        self.assertEqual(len(ET.fromstring(exports[0]).findall('g:trk/g:trkseg',ns)),2)
        self.assertEqual(len(ET.fromstring(exports[1]).findall('g:trk/g:trkseg',ns)),3)

    def test_failed_render_is_not_remembered(self):
        net=network();original=net.legs;calls=0
        def once(*args):
            nonlocal calls
            calls+=1
            if calls==1:raise ValueError('일시적 지도 생성 실패')
            return original(*args)
        with patch.object(net,'legs',side_effect=once):
            with self.assertRaises(ValueError):walking_svg(net,('A',))
            value=walking_svg(net,('A',))
            self.assertEqual(value,walking_svg(net,('A',)))
            self.assertEqual(calls,2)

    def test_concurrent_identical_requests_share_one_render(self):
        net=network();original=net.legs
        def slow(*args):
            time.sleep(.02)
            return original(*args)
        with patch.object(net,'legs',side_effect=slow) as legs:
            with ThreadPoolExecutor(max_workers=8) as pool:
                outputs=list(pool.map(lambda _:walking_svg(net,('A','B')),range(8)))
            self.assertEqual(len(set(outputs)),1)
            self.assertEqual(legs.call_count,1)

    def test_render_storage_stays_bounded_and_evicted_results_regenerate(self):
        net=network();first=walking_svg(net,('A',))
        for route in permutations(('A','B','C')):
            for home in (False,True):route_gpx(net,route,home)
        self.assertLessEqual(len(net._render_cache),RENDER_CACHE_SIZE)
        with patch.object(net,'legs',wraps=net.legs) as legs:
            self.assertEqual(first,walking_svg(net,('A',)))
            self.assertEqual(legs.call_count,1)


if __name__=='__main__':unittest.main()
