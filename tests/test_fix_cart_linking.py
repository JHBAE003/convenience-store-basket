from copy import deepcopy
from dataclasses import replace
from unittest import TestCase

from core.cart_models import CartItem, CatalogLink
from core.models import StoreProduct
from core.services.cart_linking import attach_confirmed_products
from core.services.catalog import catalog_link_for


class ConfirmedCartLinkingTests(TestCase):
    def setUp(self):
        self.cart=[CartItem('drink','제조사)검증음료 레몬250ml',3), CartItem('snack','검증 과자60g',2)]
        self.cu=StoreProduct('CU','','','제조사)검증음료 레몬250ml',product_code='CU-1',
                             manufacturer='제조사',volume='250ml',flavor='레몬',pack_count='1',
                             raw_payload={'identifier_source':'inventory_service'})
        self.gs=replace(self.cu,brand='GS25',product_code='GS-1',product_name='제조사)검증음료 레몬0.25L')
        self.emart=replace(self.cu,brand='이마트24',product_code='EM-1')
        self.links=[catalog_link_for(self.cu,'drink'),
                    CatalogLink('snack','CU','','S-1','검증 과자60g'),
                    CatalogLink('unused','GS25','','GS-1','제조사)검증음료 레몬250ml')]

    def attach(self,selected=None,**changes):
        args=dict(cart=self.cart,links=self.links,item_id='drink',selected=[self.gs] if selected is None else selected,confirmed=True)
        args.update(changes)
        return attach_confirmed_products(**args)

    def test_adds_other_brand_preserves_metadata_and_all_input_values(self):
        before=deepcopy((self.cart,self.links,self.gs))
        result=self.attach()
        self.assertEqual((self.cart,self.links,self.gs),before)
        self.assertIsNot(result,self.links)
        self.assertEqual(len(result),4)
        new=next(link for link in result if link.item_id=='drink' and link.brand=='GS25')
        self.assertEqual(new.product_code,'GS-1')
        self.assertEqual((new.manufacturer,new.volume,new.flavor,new.pack_count),('제조사','250ml','레몬','1'))
        self.assertEqual(new.identifier_source,'inventory_service')
        self.assertEqual(self.cart[0].quantity,3)
        self.assertIn(self.links[0],result)
        self.assertIn(self.links[1],result)
        self.assertIn(self.links[2],result)  # 비활성 연결은 중복 판정 대상이 아니다.

    def test_replaces_selected_brand_only_and_keeps_other_brand(self):
        links=self.links+[catalog_link_for(self.gs,'drink'),catalog_link_for(self.emart,'drink')]
        replacement=replace(self.gs,product_code='GS-CORRECTED',barcode='명시 바코드')
        result=self.attach([replacement],links=links)
        target=[link for link in result if link.item_id=='drink']
        self.assertEqual({link.brand for link in target},{'CU','GS25','이마트24'})
        self.assertEqual(next(link.product_code for link in target if link.brand=='GS25'),'GS-CORRECTED')
        self.assertIn(catalog_link_for(self.emart,'drink'),result)
        self.assertIn(catalog_link_for(self.gs,'drink'),links)

    def test_requires_explicit_confirmation_and_existing_unique_target(self):
        for changes in ({'confirmed':False},{'confirmed':None},{'confirmed':1},
                        {'item_id':'missing'},{'cart':self.cart+[self.cart[0]]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.attach(**changes)

    def test_rejects_empty_selection_duplicate_brand_and_missing_identifier(self):
        for selected in ([],[self.gs,self.gs],[replace(self.gs,product_code='',barcode='')],
                         [replace(self.gs,brand='')],[replace(self.gs,product_name='')]):
            with self.subTest(selected=selected),self.assertRaises(ValueError):
                self.attach(selected=selected)

    def test_barcode_only_selection_is_supported(self):
        row=replace(self.gs,product_code='',barcode='8801007021652')
        result=self.attach([row])
        linked=next(link for link in result if link.item_id=='drink' and link.brand=='GS25')
        self.assertEqual(linked.product_code,'')
        self.assertEqual(linked.barcode,'8801007021652')

    def test_rejects_explicit_variant_conflict_against_existing_anchor(self):
        cases=[replace(self.gs,volume='500ml',product_name='제조사)검증음료 레몬500ml'),
               replace(self.gs,flavor='포도',product_name='제조사)검증음료 포도250ml'),
               replace(self.gs,pack_count='2',product_name='제조사)검증음료 레몬250ml 2개입'),
               replace(self.gs,manufacturer='다른제조사',product_name='다른제조사)검증음료 레몬250ml')]
        for row in cases:
            with self.subTest(row=row),self.assertRaises(ValueError):
                self.attach([row])

    def test_rejects_conflict_between_selected_candidates_when_anchor_is_unspecified(self):
        cart=[CartItem('drink','검증음료',1)]
        links=[CatalogLink('drink','CU','','C','검증음료')]
        candidates=[self.gs,replace(self.emart,volume='500ml',product_name='제조사)검증음료 레몬500ml')]
        with self.assertRaises(ValueError):
            self.attach(candidates,cart=cart,links=links)

    def test_rejects_candidate_already_linked_to_another_active_item(self):
        links=self.links+[catalog_link_for(self.gs,'snack')]
        before=deepcopy(links)
        with self.assertRaisesRegex(ValueError,'이미 담긴'):
            self.attach(links=links)
        self.assertEqual(links,before)

    def test_explicit_confirmation_accepts_uncertain_names_without_fuzzy_inference(self):
        row=StoreProduct('GS25','','','확인한 다른 표기',product_code='G')
        result=self.attach([row])
        self.assertIn(catalog_link_for(row,'drink'),result)
        with self.assertRaises(ValueError):
            self.attach([row],confirmed=False)

    def test_existing_cart_label_is_anchor_even_without_prior_link(self):
        row=StoreProduct('GS25','','','검증 음료500ml',product_code='G')
        with self.assertRaises(ValueError):
            self.attach([row],cart=[CartItem('drink','검증 음료250ml',1)],links=[])

    def test_other_item_same_code_in_different_brand_is_not_a_namespace_collision(self):
        links=self.links+[CatalogLink('snack','CU','','GS-1','검증음료 레몬250ml')]
        result=self.attach(links=links)
        self.assertIn(catalog_link_for(self.gs,'drink'),result)

    def test_duplicate_preserved_brand_connections_are_rejected(self):
        links=self.links+[catalog_link_for(self.cu,'drink')]
        with self.assertRaises(ValueError):
            self.attach(links=links)

    def test_rejects_duplicate_identity_even_when_maker_alias_differs(self):
        cart=[CartItem('a','칠성)펩시콜라600ml',1),CartItem('b','롯데)펩시콜라600ml',1)]
        existing=CatalogLink('b','GS25','','8801056193010','롯데)펩시콜라600ml')
        selected=StoreProduct('GS25','','','칠성)펩시콜라600ml',product_code='8801056193010')
        links=[existing]
        before=deepcopy((cart,links,selected))
        with self.assertRaisesRegex(ValueError,'이미 담긴'):
            attach_confirmed_products(cart,links,'a',[selected],True)
        self.assertEqual((cart,links,selected),before)

    def test_rejects_shared_barcode_with_other_code_and_alias_on_another_item(self):
        cart=[CartItem('a','칠성)펩시콜라600ml',1),CartItem('b','롯데)펩시콜라600ml',1)]
        links=[CatalogLink('b','GS25','08801056193010','OLD-CODE','롯데)펩시콜라600ml')]
        selected=StoreProduct('GS25','','','칠성)펩시콜라600ml',barcode='8801056193010',product_code='NEW-CODE')
        with self.assertRaisesRegex(ValueError,'이미 담긴'):
            attach_confirmed_products(cart,links,'a',[selected],True)

    def test_internal_code_is_not_compared_to_another_items_barcode(self):
        cart=[CartItem('a','확인한 상품',1),CartItem('b','별도 상품',1)]
        links=[CatalogLink('b','GS25','same-text','','별도 상품')]
        selected=StoreProduct('GS25','','','확인한 상품',product_code='same-text')
        linked=attach_confirmed_products(cart,links,'a',[selected],True)
        self.assertEqual(len(linked),2)
