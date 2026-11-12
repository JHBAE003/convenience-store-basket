from dataclasses import replace
from io import BytesIO
import unittest
from unittest.mock import patch, Mock

from PIL import Image
from core.models import StoreProduct
from core.services.product_list import load_photo_catalog, list_products, local_photo
from core.services.integration_service import integrate_records
from core.scrapers.public_event_scraper import _CuProducts
from core.ui.product_images import product_photo, remote_photo


class ProductListTests(unittest.TestCase):
    def test_snapshot_has_real_photos_but_no_inventory_claims(self):
        products = load_photo_catalog()
        self.assertGreaterEqual(len(products), 6)
        for row in products:
            self.assertFalse(row.has_store_identity)
            self.assertFalse(row.stock_known)
            self.assertEqual(row.data_source, 'CATALOG')
            self.assertIsNotNone(local_photo(row))
            Image.open(local_photo(row)).verify()

    def test_name_search_ignores_spaces_and_latin_case(self):
        row = StoreProduct('CU', 'A', '', '펩시콜라 Zero 600ml')
        self.assertEqual(list_products([row], '펩 시 콜라'), [row])
        self.assertEqual(list_products([row], 'ZERO'), [row])
        self.assertEqual(list_products([row], '없는제품'), [])

    def test_dedup_keeps_photo_without_merging_brand_or_volume(self):
        row = StoreProduct('CU', 'A', '', '콜라500ml', barcode='123', price=1800)
        photo = replace(row, store_name='B', image_url='https://example.com/cola.png')
        other = replace(row, brand='GS25')
        variant = replace(row, barcode='456', product_name='콜라600ml')
        result = list_products([row, photo, other, variant])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].image_url, photo.image_url)
        self.assertEqual(len(list_products(result, brand='GS25')), 1)

    def test_no_approximate_photo_reuse_and_no_demo_real_photos(self):
        row = load_photo_catalog()[0]
        self.assertIsNone(local_photo(replace(row, barcode='mismatch', product_code='mismatch')))
        self.assertIsNone(local_photo(replace(row, data_source='TEST')))

    def test_failed_photo_preserves_product_with_placeholder(self):
        row = StoreProduct('CU', '', '', '사진 없는 상품', image_url='https://example.com/broken.jpg')
        with patch('core.ui.product_images.remote_photo', return_value=None):
            data, available = product_photo(row)
        self.assertFalse(available)
        self.assertEqual(Image.open(BytesIO(data)).size, (640, 420))

    def test_matching_promotion_photo_is_retained_in_inventory(self):
        stock = StoreProduct('CU', 'A', '', '음료', barcode='123', promotion_known=False)
        promo = StoreProduct('CU', '', '', '음료', barcode='123', image_url='https://example.com/product.png')
        self.assertEqual(integrate_records([stock], [promo])[0].image_url, promo.image_url)

    def test_cu_badge_does_not_replace_product_photo(self):
        parser = _CuProducts()
        parser.feed('<li class="prod_list"><img src="//example.com/product/123.jpg">'
                    '<div class="name"><p>음료</p></div><img src="/images/tag_best.png"></li>')
        self.assertEqual(parser.items[0]['image'], '//example.com/product/123.jpg')

    def test_invalid_remote_sources_do_not_make_requests(self):
        with patch('core.ui.product_images.requests.get') as get:
            for url in ('file:///C:/secret', 'http://127.0.0.1/x', 'http://localhost/x', 'http://['):
                self.assertIsNone(remote_photo(url))
            get.assert_not_called()
