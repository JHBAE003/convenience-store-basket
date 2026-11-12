"""공공자료 참고 조회. 재고·Offer를 생성하거나 기존 추천 가격을 덮어쓰지 않는다."""
from collections import deque
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import os
import re
import time
import xml.etree.ElementTree as ET

KST = timezone(timedelta(hours=9))
SOURCES = {
    'price': ('한국소비자원 참가격', 'https://www.data.go.kr/data/15158701/openapi.do'),
    'stores': ('소상공인시장진흥공단', 'https://www.data.go.kr/data/15012005/openapi.do'),
    'catalogue': ('코레일유통 상품 목록 · 2026-09-28판', 'https://www.data.go.kr/data/15168114/fileData.do'),
    'nutrition': ('식품의약품안전처', 'https://www.data.go.kr/data/15127578/openapi.do'),
}
# 경로와 키 이름은 공개 명세의 대소문자와 .do 유무를 그대로 사용한다.
ROUTES = {
    'goods': ('price', 'https://apis.data.go.kr/B551919/ProductPriceInfoService/getProductInfoSvc.do', 'ServiceKey', ('goodId', 'goodName')),
    'entities': ('price', 'https://apis.data.go.kr/B551919/ProductPriceInfoService/getStoreInfoSvc.do', 'ServiceKey', ('entpId', 'entpName')),
    'prices': ('price', 'https://apis.data.go.kr/B551919/ProductPriceInfoService/getProductPriceInfoSvc', 'serviceKey', ('goodId', 'entpId', 'goodPrice')),
    'codes': ('stores', 'https://apis.data.go.kr/B553077/api/open/sdsc2/smallUpjongList', 'servicekey', ('indsSclsCd', 'indsSclsNm')),
    'nearby': ('stores', 'https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInRadius', 'ServiceKey', ('bizesId', 'bizesNm')),
    'catalogue': ('catalogue', 'https://api.odcloud.kr/api/15168114/v1/uddi:948a104f-44a9-4591-9d02-ca6dcce16c7a', 'serviceKey', ('상품코드', '상품명')),
    'nutrition': ('nutrition', 'https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03/getFoodNtrCpntDbInq03', 'serviceKey', ('FOOD_CD', 'FOOD_NM_KR')),
}
DEFAULT_TTL = {'price': 21600, 'stores': 86400, 'catalogue': 86400, 'nutrition': 604800}


@dataclass(frozen=True)
class Config:
    keys: dict = field(default_factory=dict, repr=False)
    ttl: dict = field(default_factory=lambda: dict(DEFAULT_TTL))
    timeout: float = 8.0

    @classmethod
    def load(cls, values=None):
        values = values or {}
        common = os.environ.get('PUBLIC_DATA_SERVICE_KEY') or values.get('service_key', '')
        keys = {}
        for source in SOURCES:
            value = os.environ.get('PUBLIC_' + source.upper() + '_KEY') or values.get(source + '_key') or common
            value = str(value or '').strip()
            if value.startswith(('YOUR_', '여기에')):
                value = ''
            keys[source] = unquote(value)  # 인코딩 키도 한 번만 해제하고 urlencode로 전송한다.
        ttl = {}
        for source, default in DEFAULT_TTL.items():
            try:
                ttl[source] = max(30, min(int(values.get(source + '_ttl', default)), 604800))
            except (ValueError, TypeError):
                ttl[source] = default
        return cls(keys, ttl)


@dataclass(frozen=True)
class Result:
    source: str
    status: str = 'ok'
    records: tuple = ()
    received_at: str = ''
    cached: bool = False
    total: int | None = None
    partial: bool = False
    message: str = ''
    payload: object = field(default=None, repr=False)
    page_receipts: tuple = ()
    error_code: str = ''

    @property
    def ok(self):
        return self.status in ('ok', 'empty', 'partial')


class DataError(ValueError):
    pass


def _xml(element):
    children = list(element)
    if not children:
        return (element.text or '').strip()
    output = {}
    for child in children:
        tag = child.tag.rsplit('}', 1)[-1]
        value = _xml(child)
        if tag in output:
            if not isinstance(output[tag], list):
                output[tag] = [output[tag]]
            output[tag].append(value)
        else:
            output[tag] = value
    return output


def decode_payload(raw):
    """JSON과 XML 응답을 같은 구조로 읽되 알 수 없는 응답은 빈 결과로 만들지 않는다."""
    if len(raw) > 3_000_000:
        raise DataError('응답이 허용 크기를 넘었습니다. 조회 범위를 줄여 주세요.')
    text = raw.decode('utf-8-sig').strip()
    try:
        if text.startswith(('{', '[')):
            return json.loads(text)
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise DataError('해석할 수 없는 자료 형식입니다.')
        root = ET.fromstring(text)
        return {root.tag.rsplit('}', 1)[-1]: _xml(root)}
    except (ET.ParseError, json.JSONDecodeError, UnicodeError) as exc:
        raise DataError('외부 서비스의 응답을 해석하지 못했습니다.') from exc


def scalar(payload, names):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in names and not isinstance(value, (dict, list)):
                return value
        for value in payload.values():
            found = scalar(value, names)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = scalar(value, names)
            if found is not None:
                return found
    return None


def collect_records(payload, required):
    rows = []
    def visit(value):
        if isinstance(value, dict):
            if all(k in value and not isinstance(value[k], (dict, list)) for k in required):
                rows.append(dict(value))
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(payload)
    return tuple(rows)


def _explicit_empty(value):
    if isinstance(value, dict):
        return any((k in ('result', 'items', 'item', 'data') and v in (None, '', [], {})) or _explicit_empty(v)
                   for k, v in value.items())
    if isinstance(value, list):
        return any(_explicit_empty(v) for v in value)
    return False


def parse_records(payload, required):
    code = str(scalar(payload, ('resultCode', 'returnReasonCode')) or '').strip()
    if isinstance(payload, dict) and 'code' in payload and 'msg' in payload:
        code = str(payload['code'])
    if code in ('03', 'INFO-200'):
        return (), 0
    if code and code not in ('00', '000', '0000', '0', '200', 'INFO-000'):
        # 원본 메시지에는 인증키/요청 URL이 포함될 수 있어 화면·로그에 그대로 내보내지 않는다.
        raise DataError('외부 서비스에서 요청을 거절했습니다. 승인 상태·조회 조건·호출 한도를 확인해 주세요.')
    count = scalar(payload, ('totalCount',))
    try:
        total = int(count) if count is not None else None
    except (ValueError, TypeError):
        total = None
    rows = collect_records(payload, required)
    if not rows and total != 0 and not _explicit_empty(payload):
        raise DataError('필수 항목이 없는 응답입니다. 결과 없음으로 확정하지 않습니다.')
    return rows, total


def redact(value, secrets):
    if isinstance(value, dict):
        return {k: ('[삭제]' if k.lower() in ('servicekey', 'authorization', 'apikey') else redact(v, secrets))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[삭제]')
        return value
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """세션마다 소유한다. 키·조건별 캐시와 진행 중 요청을 공유하고 오류는 30초 보관한다."""
    def __init__(self, config):
        self.config = config
        self._cache, self._running, self._next, self._cooldown = {}, {}, {}, {}
        self._lock = Lock()
        self.metrics = deque(maxlen=100)
        self._opener = build_opener(_NoRedirect())

    def fetch(self, operation, params=None, force=False):
        source, url, key_name, required = ROUTES[operation]
        key = self.config.keys.get(source, '')
        if not key:
            return Result(source, 'unconfigured', message='공공자료 인증키를 설정하고 해당 API를 활용신청해 주세요.')
        params = dict(params or {})
        identity = (operation, tuple(sorted((k, str(v)) for k, v in params.items())))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(identity)
            if cached and now < cached[0] and (not force or not cached[1].ok):
                return replace(deepcopy(cached[1]), cached=True)
            if now < self._cooldown.get(source, 0):
                return Result(source, 'error', message='외부 서비스 재요청 대기 중입니다. 잠시 후 다시 시도해 주세요.')
            future = self._running.get(identity)
            owner = future is None
            if owner:
                future = self._running[identity] = Future()
        if not owner:
            return deepcopy(future.result())
        result = None
        try:
            result = self._request(source, url, key_name, key, params, required)
            with self._lock:
                if len(self._cache) >= 64:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[identity] = (time.monotonic() + (self.config.ttl[source] if result.ok else 30), deepcopy(result))
                if not result.ok:
                    self._cooldown[source] = time.monotonic() + 30
            future.set_result(result)
            return deepcopy(result)
        except Exception:
            result = Result(source, 'error', message='공공자료 조회를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.')
            with self._lock:
                self._cache[identity] = (time.monotonic() + 30, result)
                self._cooldown[source] = time.monotonic() + 30
            future.set_result(result)
            return result
        finally:
            with self._lock:
                self._running.pop(identity, None)

    def _request(self, source, url, key_name, key, params, required):
        params[key_name] = key
        request = Request(url + '?' + urlencode(params), headers={'User-Agent': 'ConvenienceBasket/3.2.0', 'Accept': 'application/json, application/xml'})
        for attempt in range(2):
            with self._lock:
                now = time.monotonic()
                delay = max(0, self._next.get(source, 0) - now)
                self._next[source] = now + delay + 0.3
            if delay:
                time.sleep(delay)
            started = time.monotonic()
            success = False
            payload, received, error_code = None, '', ''
            try:
                with self._opener.open(request, timeout=self.config.timeout) as response:
                    raw = response.read(3_000_001)
                received = datetime.now(KST).isoformat(timespec='seconds')
                payload = redact(decode_payload(raw), self.config.keys.values())
                rows, total = parse_records(payload, required)
                success = True
                return Result(source, 'ok' if rows else 'empty', rows, received, total=total, payload=payload)
            except HTTPError as exc:
                if attempt == 0 and (exc.code == 429 or 500 <= exc.code < 600):
                    time.sleep(0.5)
                    continue
                message = '공공자료 서버 요청이 거절되었습니다. 승인 상태·호출 한도를 확인해 주세요.'
                error_code = 'HTTP_' + str(exc.code)
            except (URLError, TimeoutError, OSError):
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                message = '공공자료 서버에 연결하지 못했습니다. 저장된 시연 자료로 대체하지 않습니다.'
            except (DataError, UnicodeError) as exc:
                message = str(exc) if isinstance(exc, DataError) else '외부 응답의 문자 형식을 해석하지 못했습니다.'
                error_code = str(scalar(payload, ('resultCode', 'returnReasonCode')) or '')
                if isinstance(payload, dict) and 'msg' in payload:
                    error_code = str(payload.get('code', error_code))
            finally:
                self.metrics.append({'source': source, 'operation_ms': round((time.monotonic() - started) * 1000),
                                     'success': success, 'attempt': attempt + 1})
            return Result(source, 'error', received_at=received, message=message, payload=payload, error_code=error_code)


def brand_of(name):
    upper = str(name).upper()
    for brand, pattern in [('GS25', r'GS\s*25'), ('CU', r'(?<![A-Z])CU(?![A-Z])|씨유'),
                           ('세븐일레븐', r'세븐일레븐|7.?ELEVEN'), ('이마트24', r'이마트\s*24|EMART\s*24')]:
        if re.search(pattern, upper):
            return brand
    return ''


def source_date(value):
    value = str(value or '').strip()
    try:
        if re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value, '%Y%m%d').date().isoformat()
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ''


def valid_gtin(value):
    """선행 0을 보존한다. 길이/체크섬이 유효해도 같은 규격이라는 추가 근거는 필요하다."""
    value = str(value).strip()
    if not value.isascii() or not value.isdigit() or len(value) not in (8, 12, 13, 14):
        return False
    digits = [int(x) for x in value]
    return (sum(v * (3 if i % 2 == 0 else 1) for i, v in enumerate(reversed(digits[:-1]))) + digits[-1]) % 10 == 0


class PublicData:
    def __init__(self, config):
        self.client = Client(config)

    def products(self, keyword, force=False):
        result = self.client.fetch('goods', force=force)
        words = str(keyword).casefold().split()
        return replace(result, records=tuple(r for r in result.records if all(w in str(r.get('goodName', '')).casefold() for w in words)))

    def entities(self, force=False):
        return self.client.fetch('entities', force=force)

    def prices(self, product_id, day, force=False):
        if not re.fullmatch(r'[\w-]{1,80}', str(product_id)) or not source_date(day):
            return Result('price', 'error', message='상품과 조사일을 확인해 주세요.')
        result = self.client.fetch('prices', {'goodId': product_id, 'goodInspectDay': source_date(day).replace('-', '')}, force)
        rows = tuple(r for r in result.records if str(r.get('goodId')) == str(product_id)
                     and (not source_date(r.get('goodInspectDay')) or source_date(r.get('goodInspectDay')) == source_date(day)))
        return replace(result, records=rows, message=('상품/조사일이 다른 행을 제외했습니다.' if len(rows) != len(result.records) else result.message))

    def _pages(self, operation, params, pages, start=1, size=1000, force=False):
        records, payloads, receipts = [], [], []
        first = None
        for page in range(start, start + pages):
            page_name, size_name = ('page', 'perPage') if operation == 'catalogue' else ('pageNo', 'numOfRows')
            result = self.client.fetch(operation, dict(params, **{page_name: page, size_name: size}), force)
            if not result.ok:
                return replace(first, records=tuple(records), partial=True, status='partial', message='일부 페이지를 받지 못했습니다.', payload=payloads, page_receipts=tuple(receipts), cached=all(c for _, c in receipts)) if first else result
            first = first or result
            records.extend(result.records)
            payloads.append(result.payload)
            receipts.append((result.received_at, result.cached))
            if len(result.records) < size or (result.total is not None and page * size >= result.total):
                break
        # 시작 페이지가 1보다 크면 마지막 페이지여도 전체 목록을 확인한 것이 아니다.
        # 같은 행이 반복되더라도 전체 범위를 확인했다고 간주하지 않는다.
        unique = {json.dumps(r, sort_keys=True, ensure_ascii=False): r for r in records}
        records = list(unique.values())
        partial = start > 1 or (first.total is not None and len(records) < first.total) or len(result.records) == size
        if start == 1 and first.total is not None and len(records) >= first.total:
            partial = False
        return replace(first, records=tuple(records), partial=partial, status='partial' if partial else first.status,
                       cached=all(c for _, c in receipts), page_receipts=tuple(receipts), payload=payloads)

    def nearby(self, lat, lng, radius, force=False):
        if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and isfinite(v) for v in (lat, lng, radius)) or not (-90 <= lat <= 90 and -180 <= lng <= 180 and 100 <= radius <= 2000):
            return Result('stores', 'error', message='좌표와 반경(100~2,000m)을 확인해 주세요.')
        codes = self.client.fetch('codes')
        if not codes.ok:
            return codes
        code = next((str(r['indsSclsCd']) for r in codes.records if str(r['indsSclsNm']).strip() == '편의점'), None)
        if not code:
            return Result('stores', 'error', message='공식 업종표에서 편의점 코드를 확인하지 못했습니다.')
        return self._pages('nearby', {'cx': lng, 'cy': lat, 'radius': int(radius), 'indsSclsCd': code, 'type': 'json'}, 3, size=500, force=force)

    def catalogue(self, start=1, pages=1, force=False):
        if type(start) is not int or type(pages) is not int or not (1 <= start <= 200 and 1 <= pages <= 3):
            return Result('catalogue', 'error', message='조회 페이지 범위를 확인해 주세요.')
        return self._pages('catalogue', {'returnType': 'JSON'}, pages, start, force=force)

    def nutrients(self, keyword, force=False):
        keyword = str(keyword).strip()
        if not 1 <= len(keyword) <= 80:
            return Result('nutrition', 'error', message='식품명을 입력해 주세요.')
        result = self.client.fetch('nutrition', {'FOOD_NM_KR': keyword, 'pageNo': 1, 'numOfRows': 100, 'type': 'json'}, force)
        return replace(result, partial=result.total is not None and result.total > len(result.records))


def price_rows(prices, entities, product_name, brands, today=None):
    """조사자료를 별도 표시한다. Offer 생성·재고 보완·현재가 추정은 하지 않는다."""
    today = today or datetime.now(KST).date().isoformat()
    stores = {str(r['entpId']): r for r in entities.records}
    output = []
    for r in prices.records:
        entity = stores.get(str(r.get('entpId')), {})
        name = str(entity.get('entpName') or '판매점 미확인 (' + str(r.get('entpId', '')) + ')')
        brand = brand_of(name)
        if brand not in brands:
            continue
        start, end = source_date(r.get('goodDcStartDay')), source_date(r.get('goodDcEndDay'))
        if str(r.get('goodDcYn', '')).upper() == 'Y':
            state = '기간 미확인'
            if start and end:
                state = '종료' if end < today else ('시작 전' if start > today else '자료상 기간 내')
        else:
            state = '미확인' if str(r.get('goodDcYn', '')).upper() != 'N' else '조사 당시 할인 아님'
        try:
            value = int(str(r.get('goodPrice', '')).replace(',', ''))
            price = f'{value:,}원' if value > 0 else '미확인'
        except ValueError:
            price = '미확인'
        output.append({'브랜드': brand, '판매점': name, '범위': '본사 자료' if '본사' in name else '조사 판매점',
                       '상품': product_name, '조사 가격': price, '가격 조사일': source_date(r.get('goodInspectDay')) or '미제공',
                       '할인 상태': state, '시작일': start or '미제공', '종료일': end or '미제공',
                       '1+1 표시': {'Y': '있음', 'N': '없음'}.get(str(r.get('plusoneYn', '')).upper(), '미확인'),
                       '추천 계산': '사용하지 않음'})
    return output
