"""공개 명세를 바탕으로 만든 고정 예시의 순수 파싱 검사. LIVE/전송/UI 실행 아님."""
import unittest
from planner.public_data import (decode_payload, parse_records, DataError, Result,
                                 price_rows, source_date, valid_gtin, brand_of, redact)


class PublicRecordsTests(unittest.TestCase):
    def test_xml_price_records_preserve_zero_and_missing_origin_time(self):
        data = decode_payload(b'<response><resultCode>00</resultCode><result><goodPriceVO><goodId>1</goodId><entpId>2</entpId><goodPrice>0</goodPrice></goodPriceVO><goodPriceVO><goodId>1</goodId><entpId>3</entpId><goodPrice>1500</goodPrice></goodPriceVO></result></response>')
        rows, total = parse_records(data, ('goodId', 'entpId', 'goodPrice'))
        self.assertEqual([r['goodPrice'] for r in rows], ['0', '1500'])
        self.assertIsNone(total)
        self.assertNotIn('goodInspectDay', rows[0])

    def test_kca_namespaced_container(self):
        data = {'result': {'iros.openapi.service.vo.entpInfoVO': [{'entpId': 'CU-1', 'entpName': 'CU(본사)'}]}, 'resultCode': '00'}
        rows, _ = parse_records(data, ('entpId', 'entpName'))
        self.assertEqual(rows[0]['entpName'], 'CU(본사)')

    def test_catalogue_barcode_and_internal_code_remain_distinct(self):
        data = decode_payload('{"totalCount":2,"data":[{"상품코드":123,"상품명":"물","바코드":"036000291452"}]}'.encode())
        rows, total = parse_records(data, ('상품코드', '상품명'))
        self.assertEqual(total, 2)
        self.assertEqual(rows[0]['바코드'], '036000291452')
        self.assertEqual(rows[0]['상품코드'], 123)

    def test_nutrition_nested_rows_keep_serving_basis(self):
        data = {'response': {'header': {'resultCode': '00'}, 'body': {'totalCount': 1, 'items': {'item': [{'FOOD_CD': 'a', 'FOOD_NM_KR': '우유', 'SERVING_SIZE': '100ml', 'AMT_NUM3': ''}]}}}}
        rows, total = parse_records(data, ('FOOD_CD', 'FOOD_NM_KR'))
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['SERVING_SIZE'], '100ml')
        self.assertEqual(rows[0]['AMT_NUM3'], '')

    def test_normal_empty_results(self):
        for data in ({'totalCount': 0, 'data': []}, {'resultCode': '00', 'result': ''}, {'resultCode': '03'}):
            self.assertFalse(parse_records(data, ('goodId', 'goodName'))[0])

    def test_auth_failure_is_not_empty(self):
        for data in ({'resultCode': '30', 'resultMsg': 'key=secret'}, {'code': -401, 'msg': 'invalid key'}):
            with self.assertRaises(DataError) as error:
                parse_records(data, ('goodId',))
            self.assertNotIn('secret', str(error.exception))

    def test_unknown_shape_is_not_empty(self):
        with self.assertRaises(DataError):
            parse_records({'resultCode': '00', 'mystery': 'unknown'}, ('goodId',))

    def test_reject_xml_entities_and_html(self):
        with self.assertRaises(DataError):
            decode_payload(b'<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>')
        with self.assertRaises(DataError):
            parse_records(decode_payload(b'<html><body>busy</body></html>'), ('goodId',))

    def test_real_dates_only(self):
        self.assertEqual(source_date('20261106'), '2026-11-06')
        self.assertEqual(source_date('2026-11-06'), '2026-11-06')
        for missing in ('', None, '20260230', '어제'):
            self.assertEqual(source_date(missing), '')

    def test_gtin_checksum_and_leading_zero(self):
        self.assertTrue(valid_gtin('036000291452'))
        self.assertTrue(valid_gtin('4006381333931'))
        for wrong in ('036000291453', '123', '４００６３８１３３３９３１', ''):
            self.assertFalse(valid_gtin(wrong))

    def test_brand_detection_rejects_unrelated_names(self):
        self.assertEqual(brand_of('GS25 봉천점'), 'GS25')
        self.assertEqual(brand_of('CU(본사)'), 'CU')
        self.assertEqual(brand_of('이마트24 서울대점'), '이마트24')
        self.assertEqual(brand_of('CULTURE 매장'), '')

    def test_secret_redaction_keeps_nonsecret_fields(self):
        data = {'ServiceKey': 'abc', 'nested': ['url=abc'], 'goodId': '100'}
        self.assertEqual(redact(data, ['abc']), {'ServiceKey': '[삭제]', 'nested': ['url=[삭제]'], 'goodId': '100'})

    def _prices(self, end, start='20261001'):
        prices = Result('price', records=({'goodId': 'g', 'entpId': 'CU', 'goodPrice': '1500', 'goodDcYn': 'Y', 'plusoneYn': 'Y', 'goodDcStartDay': start, 'goodDcEndDay': end},))
        stores = Result('price', records=({'entpId': 'CU', 'entpName': 'CU(본사)'},))
        return prices, stores

    def test_expired_promotion_is_labelled_and_never_an_offer(self):
        prices, stores = self._prices('20261031')
        row = price_rows(prices, stores, '우유', ['CU'], today='2026-11-01')[0]
        self.assertEqual(row['할인 상태'], '종료')
        self.assertEqual(row['범위'], '본사 자료')
        self.assertEqual(row['추천 계산'], '사용하지 않음')
        self.assertEqual(row['가격 조사일'], '미제공')
        self.assertNotIn('재고', row)

    def test_promotion_end_day_and_unknown_dates(self):
        prices, stores = self._prices('20261031')
        self.assertEqual(price_rows(prices, stores, '', ['CU'], today='2026-10-31')[0]['할인 상태'], '자료상 기간 내')
        prices, stores = self._prices('')
        self.assertEqual(price_rows(prices, stores, '', ['CU'])[0]['할인 상태'], '기간 미확인')

    def test_brand_filter_never_assigns_headquarters_to_other_brand(self):
        prices, stores = self._prices('20261031')
        self.assertEqual(price_rows(prices, stores, '', ['GS25']), [])


if __name__ == '__main__':
    unittest.main()
