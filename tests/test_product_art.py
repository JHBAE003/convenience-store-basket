from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from planner.test_data import TestDataset
from planner.product_art import ICON_DIR,icon_path,product_svg


class ProductArtTests(unittest.TestCase):
    def test_all_300_have_deterministic_unique_small_images(self):
        products=TestDataset().catalog();self.assertEqual(len(products),300)
        drawings=[]
        for p in products:
            raw=icon_path(p['id']).read_text(encoding='utf-8')
            self.assertEqual(raw,product_svg(p));self.assertLess(len(raw.encode()),2300)
            ET.fromstring(raw)
            self.assertIn(p['volume'],raw);self.assertIn(p['flavor'],raw)
            self.assertNotIn('<script',raw);self.assertNotIn('href=',raw)
            drawings.append(raw)
        self.assertEqual(len(set(drawings)),300)

    def test_codes_cannot_escape_icon_directory(self):
        for value in ('../../README','TEST-P001/../README','CU:real',None,'TEST-P999'):
            self.assertEqual(icon_path(value),ICON_DIR/'generic.svg')

    def test_xml_special_characters_are_escaped(self):
        p=dict(TestDataset().catalog()[0]);p.update(name='상품 "< & >',flavor='<script>&')
        raw=product_svg(p);ET.fromstring(raw)
        self.assertNotIn('<script>',raw);self.assertIn('&lt;script&gt;',raw)
