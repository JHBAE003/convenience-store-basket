from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core.models import DataLoadResult, StoreProduct
from core.services.product_list import load_photo_catalog, product_token

APP = Path(__file__).resolve().parents[1] / 'streamlit_app.py'


class ProductBrowserTests(unittest.TestCase):
    def setUp(self):
        # UI tests must neither depend on nor change the user's saved mappings.
        for name,options in [('load_user_catalog',{'return_value':[]}),('save_confirmed_links',{})]:
            stub=patch('core.services.catalog.'+name,**options)
            stub.start()
            self.addCleanup(stub.stop)

    def app(self):
        at = AppTest.from_file(str(APP), default_timeout=25).run()
        self.assertEqual(len(at.exception), 0)
        self.addCleanup(lambda: at.session_state['temp_directory'].cleanup())
        return at

    def test_initial_catalog_is_local_and_name_search_adds_exact_product(self):
        with patch('core.data.data_manager.DataManager.search_products') as search:
            at = self.app()
            self.assertEqual(at.radio(key='mode').value, 'LIVE')
            at.text_input(key='live_keyword').set_value('펩 시').run()
            row = next(p for p in load_photo_catalog() if '펩시' in p.product_name)
            at.button(key=f'card_{product_token(row)}').click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertEqual(at.session_state['cart'][0].label, row.product_name)
            self.assertEqual(at.session_state['links'][0].barcode, row.barcode)
            search.assert_not_called()

    def test_empty_and_brand_filters_leave_cart_intact(self):
        at = self.app()
        at.selectbox(key='catalog_brand').set_value('GS25').run()
        self.assertTrue(any('검색 결과가 없습니다' in e.value for e in at.info))
        at.selectbox(key='catalog_brand').set_value('전체').run()
        at.text_input(key='live_keyword').set_value('존재하지 않는 상품').run()
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any('검색 결과가 없습니다' in e.value for e in at.info))

    def test_failed_live_search_is_not_replaced_with_catalog(self):
        with patch('core.data.data_manager.DataManager.search_products',
                   return_value=([], [DataLoadResult('CU', [], 'ERROR', '연결 실패')])):
            at = self.app()
            at.text_input(key='live_keyword').set_value('펩시').run()
            at.button(key='search_live').click().run()
            self.assertEqual(len(at.exception), 0)
            self.assertFalse(any(b.key and b.key.startswith('card_') for b in at.button))
            self.assertTrue(any('확인하지 못했습니다' in w.value for w in at.warning))

    def test_live_search_shows_provider_photo_and_new_query_hides_old_results(self):
        row = StoreProduct('CU', '조회 매장', '', '사진 검증용 제품', barcode='123', price=1000,
                           image_url='https://example.com/product.png', data_source='LIVE')
        with patch('core.data.data_manager.DataManager.search_products',
                   return_value=([row], [DataLoadResult('CU', [row], 'LIVE', '성공')])), \
             patch('core.ui.product_images.remote_photo', return_value=None) as photo:
            at = self.app()
            at.text_input(key='live_keyword').set_value('사진 검증용').run()
            at.button(key='search_live').click().run()
            self.assertEqual(len(at.exception), 0)
            photo.assert_called_with(row.image_url)
            self.assertIsNotNone(at.button(key=f'card_{product_token(row)}'))
            at.text_input(key='live_keyword').set_value('다른 검색어').run()
            self.assertFalse(any(b.key == f'card_{product_token(row)}' for b in at.button))

    def test_same_barcode_alias_adds_quantity_to_existing_cart_item(self):
        row=StoreProduct('CU','','','검증사)검증음료250ml',barcode='TEST-ALIAS',product_code='A',data_source='LIVE')
        alias=replace(row,product_name='검증음료250ml')
        responses=[([r],[DataLoadResult('CU',[r],'LIVE','모의 상품 후보')]) for r in (row,alias)]
        with patch('core.data.data_manager.DataManager.search_products',side_effect=responses):
            at=self.app()
            at.text_input(key='live_keyword').set_value('검증').run()
            at.button(key='search_live').click().run()
            at.button(key=f'card_{product_token(row)}').click().run()
            at.button(key='search_live').click().run()
            at.button(key=f'card_{product_token(alias)}').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(len(at.session_state['cart']),1)
        self.assertEqual(at.session_state['cart'][0].quantity,2)

    def test_shared_gtin_does_not_duplicate_another_active_item_link(self):
        row=StoreProduct('GS25','','','검증사)검증음료250ml',barcode='8801007021652',product_code='G',data_source='LIVE')
        other=replace(row,brand='CU',product_code='C')
        with patch('core.data.data_manager.DataManager.search_products',side_effect=[
                ([row],[DataLoadResult('GS25',[row],'LIVE','모의 후보')]),
                ([row,other],[DataLoadResult('GS25',[row],'LIVE','모의 후보'),DataLoadResult('CU',[other],'LIVE','모의 후보')])]):
            at=self.app()
            at.text_input(key='live_keyword').set_value('검증').run()
            at.button(key='search_live').click().run()
            at.button(key=f'card_{product_token(row)}').click().run()
            at.button(key='search_live').click().run()
            at.button(key=f'card_{product_token(other)}').click().run()
        self.assertEqual(len(at.exception),0)
        self.assertEqual(len(at.session_state['cart']),2)
        links=at.session_state['links']
        self.assertEqual(len(links),2)
        self.assertEqual(len({(link.brand,link.product_code) for link in links}),2)
