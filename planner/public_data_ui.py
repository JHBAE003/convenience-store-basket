"""LIVE 전용 공공자료 패널. 버튼을 누를 때만 외부 요청을 한다."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import streamlit as st
from planner.public_data import Config, PublicData, SOURCES, KST, brand_of, price_rows, valid_gtin, scalar
from planner.engine import haversine_km


def _service():
    try:
        values = dict(st.secrets.get('public_data', {}))
    except (FileNotFoundError, KeyError):
        values = {}
    config = Config.load(values)
    # 키가 바뀌면 그 세션의 이전 요청/캐시와 연결 자료를 재사용하지 않는다.
    identity = sha256(json.dumps([config.keys, config.ttl], sort_keys=True).encode()).hexdigest()
    if st.session_state.get('public_config_identity') != identity:
        for k in list(st.session_state):
            if k.startswith('public_result_'):
                del st.session_state[k]
        st.session_state.pop('public_catalog_links', None)
        st.session_state['public_service'] = PublicData(config)
        st.session_state['public_config_identity'] = identity
    return st.session_state['public_service']


def _metadata(result):
    name, url = SOURCES[result.source]
    st.caption(f'출처: [{name}]({url})')
    if result.message:
        (st.warning if not result.ok or result.partial else st.info)(result.message)
    if not result.ok:
        return False
    if result.partial:
        st.warning('받은 범위만 표시합니다. 목록 전체를 확인한 결과가 아닙니다.')
    if result.page_receipts:
        times = sorted({t for t, _ in result.page_receipts if t})
        stamp = ' ~ '.join((times[0], times[-1])) if len(times) > 1 else (times[0] if times else '미제공')
    else:
        stamp = result.received_at or '미제공'
    st.caption(f'앱 수신 시각: {stamp} · 앱 캐시: '+('전체 사용' if result.cached else '사용 안 함 또는 일부 사용'))
    return True


def _saved(kind, context, action):
    key = 'public_result_' + kind
    if action is not None:
        with st.spinner('공공자료를 확인하고 있습니다.'):
            st.session_state[key] = (context, action())
    entry = st.session_state.get(key)
    if entry and entry[0] == context:
        return entry[1]
    if entry:
        st.caption('조회 조건이 바뀌었습니다. 새 조건으로 확인해 주세요.')
    return None


def _price(service, brands, force):
    with st.form('public_price_search'):
        keyword = st.text_input('조사 상품명', max_chars=80, placeholder='우유, 생수, 과자')
        search = st.form_submit_button('조사 상품 찾기')
    goods = _saved('goods', keyword, (lambda: service.products(keyword, force)) if search and keyword.strip() else None)
    if search and not keyword.strip():
        st.info('조사할 상품명을 입력해 주세요.')
    if goods is None or not _metadata(goods):
        return
    if not goods.records:
        st.info('받은 상품 목록에서 검색어에 맞는 후보를 찾지 못했습니다.')
        return
    candidates = {str(r['goodId']): r for r in goods.records}
    if st.session_state.get('public_good_id') not in candidates:
        st.session_state.pop('public_good_id', None)
    selected = st.selectbox('조사 상품 선택', list(candidates),
                            format_func=lambda k: f'{candidates[k]["goodName"]} · 상품 ID {k}', key='public_good_id')
    product = candidates[selected]
    st.caption('상품명·용량을 확인해 선택하세요. 공공 상품 ID는 바코드나 편의점 내부 상품 코드가 아닙니다.')
    today = datetime.now(KST).date()
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    day = st.date_input('가격 조사일', value=friday, max_value=today, key='public_survey_day')
    st.caption('조사일에 자료가 없을 수 있습니다. 오늘의 매장 확정 가격·재고가 아닙니다.')
    requested = st.button('선택 상품의 조사 가격 확인', key='public_price_lookup')
    pair = _saved('price', (selected, day.isoformat(), tuple(brands)),
                  (lambda: (service.prices(selected, day.isoformat(), force), service.entities(force))) if requested else None)
    if pair is None:
        return
    prices, entities = pair
    if not _metadata(prices):
        return
    if not entities.ok:
        _metadata(entities)
        st.warning('판매점 정보를 확인하지 못해 브랜드별 가격을 연결하지 않았습니다.')
        return
    st.caption('판매점명 확인 자료의 앱 수신 시각: '+(entities.received_at or '미제공')+' · 앱 캐시: '+('사용' if entities.cached else '사용 안 함'))
    rows = price_rows(prices, entities, str(product['goodName']), brands)
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch')
    else:
        st.info('이 조사일·선택 브랜드에 해당하는 가격 자료를 확인하지 못했습니다. 가격이나 행사 없음으로 확정하지 않습니다.')
    st.caption('본사 자료는 특정 점포 가격으로 적용하지 않습니다. 1+1 표시만으로 2+1·카드·회원 조건을 추정하지 않으며 추천 계산에 넣지 않습니다.')


def _stores(service, origin, brands, force):
    with st.form('public_store_form'):
        radius = st.slider('주변 검색 반경 (m)', 100, 2000, 1000, 100)
        requested = st.form_submit_button('주변 편의점 확인')
    result = _saved('stores', (origin, tuple(brands), radius),
                    (lambda: service.nearby(*origin, radius, force)) if requested else None)
    if result is None or not _metadata(result):
        return
    points = {}
    payloads = result.payload if isinstance(result.payload, list) else [result.payload]
    months = sorted({str(v) for payload in payloads if (v := scalar(payload, ('stdrYm',)))})
    reference_month = ' / '.join(months) or '미제공'
    for r in result.records:
        name = str(r.get('bizesNm', '')) + (' ' + str(r['brchNm']) if r.get('brchNm') else '')
        brand = brand_of(name)
        if brand not in brands:
            continue
        try:
            lat, lon = float(r['lat']), float(r['lon'])
            distance = haversine_km(*origin, lat, lon) * 1000
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= distance <= radius):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        points[str(r['bizesId'])] = {'브랜드': brand, '매장': name, '주소': r.get('rdnmAdr') or r.get('lnoAdr') or '미제공',
            '직선 거리 (m)': round(distance), '자료 기준월': r.get('stdrYm') or reference_month, '위도': lat, '경도': lon}
    rows = sorted(points.values(), key=lambda r: r['직선 거리 (m)'])
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch')
        st.map([{'lat': r['위도'], 'lon': r['경도']} for r in rows])
    else:
        st.info('받은 범위에서 선택한 브랜드의 매장을 확인하지 못했습니다.')
    st.caption('상호명으로 분류한 매장 후보입니다. 현재 영업·재고·출입구는 확인하지 않았으며 기존 재고 조회의 점포 코드와 자동 연결하지 않습니다.')


def _catalogue(service, products, force):
    with st.form('public_catalogue_form'):
        keyword = st.text_input('상품명 또는 정확한 바코드', max_chars=80)
        start = st.number_input('목록 시작 페이지', min_value=1, max_value=200, value=1, step=1)
        pages = st.selectbox('받을 페이지 수 (페이지당 최대 1,000행)', (1, 2, 3))
        requested = st.form_submit_button('상품 목록에서 찾기')
    # 공개 명세에 상품명/바코드 검색 변수가 없어 받은 페이지에서만 검색한다.
    if requested and not keyword.strip():
        st.info('상품명이나 바코드를 입력해 주세요.')
    result = _saved('catalogue', (keyword, start, pages),
                    (lambda: service.catalogue(start, pages, force)) if requested and keyword.strip() else None)
    if result is None or not _metadata(result):
        return
    st.caption(f'{start}페이지부터 {pages}페이지 이내에서 받은 {len(result.records):,}행 중 검색합니다. 자료판: 2026-09-28 · 전체 행: '+(f'{result.total:,}' if result.total is not None else '미제공'))
    term = keyword.strip()
    found = [r for r in result.records if (str(r.get('바코드', '')) == term if term.isdigit() else all(w in str(r.get('상품명', '')).casefold() for w in term.casefold().split()))]
    rows = [{'상품명': r['상품명'], '규격': r.get('규격') or '미제공', '바코드': str(r.get('바코드') or ''),
             '코레일 내부 코드': str(r['상품코드']), '판매개시일': r.get('판매개시일') or '미제공',
             '바코드 형식': '체크섬 확인' if valid_gtin(str(r.get('바코드', ''))) else '미확인'} for r in found]
    if not rows:
        st.info('받은 페이지에서 후보를 찾지 못했습니다. 전체 목록에 상품이 없다는 뜻은 아닙니다.')
        return
    st.dataframe(rows, hide_index=True, width='stretch', column_config={'바코드': st.column_config.TextColumn()})
    st.caption('상품 코드와 바코드를 별도로 보존합니다. 가격·재고는 제공되지 않습니다.')
    if products:
        choice = st.selectbox('규격을 확인할 목록 상품', range(len(rows)), format_func=lambda k: rows[k]['상품명']+' · '+rows[k]['규격'])
        targets = {p.id: p for p in products}
        target = st.selectbox('참고 바코드를 연결할 장바구니 상품', list(targets), format_func=lambda k: targets[k].name+' · '+targets[k].spec)
        confirmation = st.checkbox('상품명·용량·맛·포장 수가 같은 것을 확인했습니다', key='public_barcode_confirm_'+str(rows[choice]['코레일 내부 코드'])+'_'+target)
        if st.button('참고 연결 저장', disabled=not confirmation or not valid_gtin(rows[choice]['바코드'])):
            st.session_state.setdefault('public_catalog_links', {})[target] = dict(rows[choice], basket_spec=targets[target].spec)
            st.success('이 세션의 참고 연결로 저장했습니다. 편의점 내부 코드나 추천 계산은 바꾸지 않습니다.')
        saved = st.session_state.get('public_catalog_links', {})
        current = [{'장바구니 상품': targets[k].name, **v} for k, v in saved.items() if k in targets and v['basket_spec'] == targets[k].spec]
        if current:
            st.dataframe(current, hide_index=True, width='stretch')


def _nutrition(service, force):
    with st.form('public_nutrition_form'):
        keyword = st.text_input('영양정보를 찾을 식품명', max_chars=80)
        requested = st.form_submit_button('영양정보 찾기')
    result = _saved('nutrition', keyword, (lambda: service.nutrients(keyword, force)) if requested else None)
    if result is None or not _metadata(result):
        return
    rows = [{'식품명': r['FOOD_NM_KR'], '업체': r.get('MAKER_NM') or '미제공', '기준량': r.get('SERVING_SIZE') or '미제공',
             '열량 (kcal)': r.get('AMT_NUM1') or '미제공', '단백질 (g)': r.get('AMT_NUM3') or '미제공',
             '지방 (g)': r.get('AMT_NUM4') or '미제공', '탄수화물 (g)': r.get('AMT_NUM6') or '미제공',
             '데이터 수정일': r.get('UPDATE_DATE') or '미제공', '품목제조보고번호': r.get('ITEM_REPORT_NO') or '미제공'} for r in result.records]
    if rows:
        st.dataframe(rows, hide_index=True, width='stretch')
    else:
        st.info('해당 식품의 영양정보를 확인하지 못했습니다.')
    st.caption('제품·제조사·기준량이 같은지 확인하세요. 값이 없는 성분을 0으로 바꾸거나 장바구니 영양 합계로 자동 계산하지 않습니다.')


def render_public_data(origin, brands, products):
    # 호출부도 LIVE로 제한한다. TEST에서는 이 모듈과 인증키를 읽지 않는다.
    if st.session_state.get('data_mode') != 'LIVE':
        return
    with st.expander('공공자료로 가격·매장 확인', expanded=False):
        service = _service()
        selected = st.selectbox('확인할 자료', ('조사 가격·할인', '주변 편의점', '상품 바코드·규격', '식품 영양정보'), key='public_kind')
        source = {'조사 가격·할인': 'price', '주변 편의점': 'stores', '상품 바코드·규격': 'catalogue', '식품 영양정보': 'nutrition'}[selected]
        if not service.client.config.keys.get(source):
            st.info('이 자료는 활용신청 후 인증키 설정이 필요합니다. README의 공공자료 설정 안내를 확인해 주세요.')
            st.link_button('활용신청 페이지', SOURCES[source][1])
            return
        force = st.checkbox('앱의 저장 결과를 건너뛰고 요청', key='public_force')
        st.caption('버튼을 눌러야 조회합니다. 재요청도 공공기관 원본의 갱신을 보장하지 않습니다.')
        if source == 'price':
            _price(service, brands, force)
        elif source == 'stores':
            _stores(service, origin, brands, force)
        elif source == 'catalogue':
            _catalogue(service, products, force)
        else:
            _nutrition(service, force)
