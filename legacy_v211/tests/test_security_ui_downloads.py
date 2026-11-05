from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from core.cart_models import CartItem,CatalogLink
from core.models import DataLoadResult,StoreProduct

APP=Path(__file__).resolve().parents[1]/'streamlit_app.py'


class SecurityUiDownloadTests(unittest.TestCase):
    def test_builtin_table_downloads_receive_safe_copies_of_external_store_names(self):
        malicious=' \n=HYPERLINK("https://example.invalid/","목록")'
        row=StoreProduct('CU',malicious,'서울','검증음료250ml',product_code='C',store_code='S',
                         latitude=37.458115,longitude=126.952161,stock=13,stock_known=True,
                         price=2500,price_data_status='REFERENCE',data_source='LIVE',
                         received_at=datetime.now(timezone.utc).isoformat())
        original_name=row.store_name
        with patch('core.services.catalog.load_user_catalog',return_value=[]), \
             patch('core.data.data_manager.DataManager.search_selected',return_value=(
                    [row],[DataLoadResult('CU',[row],'LIVE','모의 응답')])):
            at=AppTest.from_file(str(APP),default_timeout=25).run()
            self.addCleanup(lambda:at.session_state['temp_directory'].cleanup())
            at.session_state['cart']=[CartItem('i',row.product_name,1)]
            at.session_state['links']=[CatalogLink('i','CU','','C',row.product_name)]
            at.multiselect(key='brands').set_value(['CU']).run()
            at.button(key='compare').click().run()
            self.assertFalse(at.exception)
            received=next(table.value for table in at.dataframe if '재고 응답' in table.value.columns)
            comparison=next(table.value for table in at.dataframe if '확정 추천 가능' in table.value.columns)
            for frame in (received,comparison):
                self.assertTrue(frame.iloc[0]['매장'].startswith('[텍스트] '))
                self.assertIn(original_name.strip(),frame.iloc[0]['매장'])
            self.assertEqual(received.iloc[0]['재고 응답'],'13개')
            self.assertEqual(row.store_name,original_name)
            self.assertEqual(at.session_state['snapshot'].records[0].store_name,original_name)
