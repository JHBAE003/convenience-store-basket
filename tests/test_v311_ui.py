from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest
from planner.walk_map import get_network,ORIGIN

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class V311UiTests(unittest.TestCase):
    def app(self):return AppTest.from_file(str(APP),default_timeout=30).run()

    def test_default_images_real_map_and_consistent_distance(self):
        at=self.app();self.assertFalse(at.exception)
        at.button_group(key='workspace_view').set_value('추천·경로').run()
        self.assertEqual(at.session_state['last_distance_basis'],'도보 경로')
        self.assertGreaterEqual(len(at.get('image')),4)
        self.assertTrue(any('방문 경로'==e.value for e in at.subheader))
        snapshot=at.session_state['snapshot']
        points=((ORIGIN,(at.session_state['origin_lat'],at.session_state['origin_lng'])),)+tuple((s.id,(s.lat,s.lng)) for s in snapshot.stores)
        net=get_network(tuple(sorted(points)));plan=at.session_state['last_summary']['최저 결제액']
        self.assertAlmostEqual(plan['distance'],sum(l.distance_km for l in net.legs(plan['route'])))

    def test_gallery_filter_page_and_add_reuse_existing_flow(self):
        at=self.app()
        self.assertEqual(len([b for b in at.button if (b.key or '').startswith('catalog_add_')]),12)
        at.selectbox(key='catalog_category').set_value('커피').run()
        at.selectbox(key='catalog_page').set_value(3).run()
        self.assertEqual(len([b for b in at.button if (b.key or '').startswith('catalog_add_')]),1)
        at.button(key='catalog_add_TEST-P250').click().run()
        self.assertTrue(any(p.id=='TEST-P250' for p in at.session_state['products']))
        self.assertIsNone(at.session_state['snapshot'])
        at.button(key='lookup_button').click().run()
        self.assertGreater(at.session_state['last_plan_count'],0);self.assertFalse(at.exception)

    def test_outside_map_does_not_silently_use_straight_routes(self):
        at=self.app();at.number_input(key='origin_lat').set_value(37.50).run()
        at.button(key='lookup_button').click().run()
        self.assertEqual(at.session_state['last_plan_count'],0)
        self.assertTrue(any('범위 밖' in e.value for e in at.warning))
        self.assertEqual(at.session_state['last_distance_basis'],'도보 경로')
        at.radio(key='distance_basis').set_value('직선 거리 (참고)').run()
        self.assertGreater(at.session_state['last_plan_count'],0);self.assertFalse(at.exception)

    def test_return_route_and_search_images(self):
        at=self.app();before=at.session_state['last_summary']['최저 결제액']['distance']
        at.checkbox(key='return_home').check().run()
        self.assertGreater(at.session_state['last_summary']['최저 결제액']['distance'],before)
        at.text_input(key='search_keyword').set_value('우유');at.button(key='search_button').click().run()
        self.assertGreaterEqual(len(at.get('image')),5);self.assertFalse(at.exception)
