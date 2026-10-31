from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core.models import StoreProduct,DataLoadResult
from core.services.product_list import product_token

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class FixedLiveUiTests(unittest.TestCase):
    def setUp(self):
        for name,options in [('load_user_catalog',{'return_value':[]}),('save_confirmed_links',{})]:
            stub=patch('core.services.catalog.'+name,**options)
            stub.start();self.addCleanup(stub.stop)

    def app(self):
        at=AppTest.from_file(str(APP),default_timeout=25).run()
        self.addCleanup(lambda:at.session_state['temp_directory'].cleanup())
        self.assertFalse(at.exception)
        return at

    def candidates(self):
        row=StoreProduct('CU','','','검증사)검증음료250ml',product_code='C',data_source='LIVE',stock_known=False)
        other=replace(row,brand='GS25',product_code='G')
        return row,other

    def test_attach_brand_preserves_quantity_and_does_not_query_stock(self):
        row,other=self.candidates()
        with patch('core.data.data_manager.DataManager.search_products',return_value=([row,other],[])) as catalog, \
             patch('core.data.data_manager.DataManager.search_selected') as stock:
            at=self.app()
            at.text_input(key='live_keyword').set_value('검증').run()
            at.button(key='search_live').click().run()
            at.button(key=f'card_{product_token(row)}').click().run()
            item=at.session_state['cart'][0]
            at.number_input(key=f'qty_{item.item_id}').set_value(3).run()
            at.button(key=f'link_{item.item_id}').click().run()
            at.multiselect(key='live_selection').set_value([('GS25','','G',other.product_name)])
            at.checkbox(key='confirm_mapping').set_value(True)
            next(b for b in at.button if b.label=='이 상품에 연결하기').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.session_state['cart']),1)
            self.assertEqual(at.session_state['cart'][0].quantity,3)
            self.assertEqual({l.brand for l in at.session_state['links']},{'CU','GS25'})
            stock.assert_not_called()
            self.assertEqual(catalog.call_count,2)

    def test_response_table_shows_stock_price_and_outside_radius_even_without_recommendation(self):
        row,_=self.candidates()
        observation=replace(row,store_name='실제응답을모의한점포',store_code='S',address='서울',
                            price=2500,price_data_status='REFERENCE',stock=13,stock_known=True,
                            stock_data_status='LIVE',promotion_known=False,
                            received_at=datetime.now(timezone.utc).isoformat(),latitude=37.48,longitude=126.952161)
        with patch('core.data.data_manager.DataManager.search_products',return_value=([row],[])), \
             patch('core.data.data_manager.DataManager.search_selected',return_value=([observation],
                    [DataLoadResult('CU',[observation],'LIVE','수집 성공')])) as stock:
            at=self.app()
            at.text_input(key='live_keyword').set_value('검증').run()
            at.button(key='search_live').click().run()
            at.button(key=f'card_{product_token(row)}').click().run()
            at.button(key='compare').click().run()
            self.assertFalse(at.exception)
            frames=[table.value for table in at.dataframe]
            received=next(frame for frame in frames if '재고 응답' in frame.columns)
            self.assertEqual(received.iloc[0]['재고 응답'],'13개')
            self.assertEqual(received.iloc[0]['판매가'],'2,500원')
            self.assertEqual(received.iloc[0]['검색 범위'],'반경 밖')
            self.assertIsNone(at.session_state['recommendations'].cheapest)
            at.select_slider(key='radius').set_value(5.0).run()
            received=next(table.value for table in at.dataframe if '재고 응답' in table.value.columns)
            self.assertEqual(received.iloc[0]['검색 범위'],'반경 안')
            stock.assert_called_once()

    def test_fresh_session_defers_large_event_catalog(self):
        at=self.app()
        self.assertTrue(at.session_state['manager'].promotion_manager.defer_catalog)

    def test_all_brand_first_page_is_not_filled_only_with_first_brand(self):
        brands=['GS25','CU','세븐일레븐','이마트24']
        rows=[StoreProduct(brand,'','','검증 음료'+str(index),product_code=brand+str(index),
                           price=0 if brand=='GS25' else 1200,data_source='LIVE',stock_known=False)
              for brand in brands for index in range(4)]
        with patch('core.data.data_manager.DataManager.search_products',return_value=(rows,
                [DataLoadResult(brand,[r for r in rows if r.brand==brand],'LIVE','모의 성공') for brand in brands])):
            at=self.app()
            at.text_input(key='live_keyword').set_value('검증').run()
            at.button(key='search_live').click().run()
            rendered={button.key for button in at.button if button.key and button.key.startswith('card_')}
            for brand in brands:
                first=next(row for row in rows if row.brand==brand)
                self.assertIn('card_'+product_token(first),rendered)

    def test_manually_confirmed_four_brand_pepsi_labels_can_share_one_cart_item(self):
        labels={'GS25':'롯데)펩시콜라600MLPET','CU':'롯데)펩시콜라P600ml',
                '세븐일레븐':'칠성)펩시콜라600ml','이마트24':'롯데)펩시콜라펫600ml'}
        rows=[StoreProduct(brand,'','',name,product_code='8801056193010',data_source='LIVE')
              for brand,name in labels.items()]
        with patch('core.data.data_manager.DataManager.search_products',return_value=(rows,[])):
            at=self.app()
            at.text_input(key='live_keyword').set_value('펩시').run()
            at.button(key='search_live').click().run()
            at.multiselect(key='live_selection').set_value([(r.brand,'',r.product_code,r.product_name) for r in rows])
            at.checkbox(key='confirm_mapping').set_value(True)
            next(button for button in at.button if button.label=='선택 상품 담기').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.session_state['cart']),1)
            self.assertEqual({l.brand for l in at.session_state['links']},set(labels))

    def test_updating_backend_replaces_old_manager_without_erasing_cart(self):
        from core.services.product_list import load_photo_catalog
        at=self.app()
        at.text_input(key='live_keyword').set_value('펩시').run()
        row=next(p for p in load_photo_catalog() if '펩시' in p.product_name)
        at.button(key='card_'+product_token(row)).click().run()
        item=at.session_state['cart'][0]
        at.number_input(key='qty_'+item.item_id).set_value(3).run()
        previous=at.session_state['manager']
        path=previous.cache_path.parent
        links=list(at.session_state['links'])
        at.session_state['backend_revision']='구형 관리자'
        at.run()
        self.assertFalse(at.exception)
        self.assertIsNot(at.session_state['manager'],previous)
        self.assertEqual(at.session_state['cart'][0].quantity,3)
        self.assertEqual(at.session_state['links'],links)
        self.assertFalse(path.exists())
        self.assertTrue(at.session_state['manager'].promotion_manager.defer_catalog)

    def test_store_details_follow_current_snapshot_after_partial_retry(self):
        from core.cart_models import CartItem,CatalogLink
        row,_=self.candidates()
        cu=replace(row,store_name='CU 확인 점포',store_code='C1',latitude=37.458115,
                   longitude=126.952161,stock=2,stock_known=True,received_at='2026-10-31T10:00:00+09:00')
        seven=replace(cu,brand='세븐일레븐',store_name='세븐 확인 점포',store_code='S1')
        with patch('core.data.data_manager.DataManager.search_selected',side_effect=[
                ([cu],[DataLoadResult('CU',[cu],'LIVE','모의 응답')]),
                ([],[DataLoadResult('세븐일레븐',[],'ERROR','모의 실패')]),
                ([seven],[DataLoadResult('세븐일레븐',[seven],'LIVE','모의 복구')])]) as remote:
            at=self.app()
            at.session_state['cart']=[CartItem('i',row.product_name,1)]
            at.session_state['links']=[CatalogLink('i',brand,'','C',row.product_name) for brand in ('CU','세븐일레븐')]
            at.multiselect(key='brands').set_value(['CU','세븐일레븐']).run()
            at.button(key='compare').click().run()
            at.selectbox(key='store_detail_key').set_value('CU|C1').run()
            at.button(key='retry_failed_brands').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(remote.call_count,3)
            self.assertTrue(any('앱에 저장된 자료를 사용했습니다.' in caption.value for caption in at.caption))
            self.assertTrue(any('2026-10-31 10:00:00' in caption.value for caption in at.caption))

    def test_same_code_alias_cannot_be_duplicated_by_form_or_card(self):
        from core.cart_models import CartItem,CatalogLink
        row=StoreProduct('GS25','','','칠성)펩시콜라600ml',product_code='G',data_source='LIVE')
        with patch('core.data.data_manager.DataManager.search_products',return_value=([row],[])):
            at=self.app()
            at.session_state['cart']=[CartItem('old','롯데)펩시콜라600ml',1)]
            at.session_state['links']=[CatalogLink('old','GS25','','G','롯데)펩시콜라600ml')]
            at.text_input(key='live_keyword').set_value('펩시').run()
            at.button(key='search_live').click().run()
            at.multiselect(key='live_selection').set_value([('GS25','','G',row.product_name)])
            next(button for button in at.button if button.label=='선택 상품 담기').click().run()
            self.assertEqual(len(at.session_state['cart']),1)
            self.assertEqual(at.session_state['cart'][0].quantity,1)
            at.button(key='card_'+product_token(row)).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.session_state['cart']),1)
            self.assertEqual(at.session_state['cart'][0].quantity,1)

    def test_saved_group_does_not_duplicate_other_active_brand(self):
        from core.cart_models import CartItem,CatalogLink
        row,_=self.candidates()
        with patch('core.data.data_manager.DataManager.search_products',return_value=([row],[])):
            at=self.app()
            at.session_state['cart']=[CartItem('old',row.product_name,1)]
            at.session_state['links']=[CatalogLink('old','GS25','','G',row.product_name)]
            at.session_state['user_catalog']=[CatalogLink('saved',brand,'',code,row.product_name)
                                             for brand,code in [('CU','C'),('GS25','G')]]
            at.text_input(key='live_keyword').set_value('검증').run()
            at.button(key='search_live').click().run()
            at.button(key='card_'+product_token(row)).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.session_state['cart']),2)
            self.assertEqual(len(at.session_state['links']),2)
            self.assertEqual(sum(link.brand=='GS25' for link in at.session_state['links']),1)
