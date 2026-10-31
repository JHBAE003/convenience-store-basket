from dataclasses import replace
from urllib.parse import quote
from uuid import uuid4
import pandas as pd
import streamlit as st

from core.config import BRANDS, DEFAULT_LAT, DEFAULT_LNG, BASE_DIR
from core.cart_models import CartItem, CatalogLink
from core.services.catalog import load_catalog, resolve_cart_records
from core.services.query_service import new_session_manager, fetch_snapshot
from core.services.basket import evaluate_stores
from core.services.basket_recommendation import recommend_baskets
from core.services.exporting import comparison_csv
from core.services.geocoding import search_korean_address
from core.services.product_list import load_photo_catalog, list_products
from core.ui.product_browser import render_product_cards
from core.ui.v2_panels import render_v2
from core.ui.v2_actions import clear_comparison
from core.services.substitutions import load_substitute_catalog
from core.services.v2_common import apply_current_evidence

st.set_page_config(page_title='편의점 장바구니 v2',page_icon=':material/shopping_basket:',layout='wide')


def demo_links():
    links=load_catalog()
    for product in load_substitute_catalog():
        if product.barcode.startswith('DEMO-ALT-'):
            item_id='alt-drink' if product.barcode.endswith('DRINK') else 'alt-snack'
            links.append(CatalogLink(item_id,product.brand,product.barcode,product.product_code,product.product_name))
    return links


def session_manager(mode):
    return new_session_manager(mode,test_csv=BASE_DIR/'sample_data/v2_demo_products.csv')


def invalidate():
    clear_comparison()


def load_v2_scenario():
    if st.session_state.mode!='TEST':
        return
    scenario=st.session_state.v2_demo_scenario
    st.session_state.links=demo_links()
    cart=[CartItem('drink','시연 음료 250ml',1 if scenario=='행사 수량' else 2),
          CartItem('noodle','시연 컵라면 65g',1),CartItem('snack','시연 과자 60g',1)]
    st.session_state.cart=cart
    for item in cart:
        st.session_state[f'qty_{item.item_id}']=item.quantity
        st.session_state[f'locked_{item.item_id}']=scenario=='예산 맞추기' and item.item_id=='noodle'
    st.session_state.budget=4000 if scenario=='예산 맞추기' else 6000
    st.session_state.v2_tool=scenario
    if scenario=='품절 대체':
        st.session_state.v2_sub_target='drink'
        st.session_state.v2_sub_store='세븐일레븐|C'
    invalidate()
    st.session_state.snapshot=fetch_snapshot(cart,st.session_state.links,st.session_state.manager,
                                           st.session_state.latitude,st.session_state.longitude,
                                           st.session_state.brands,'TEST')


def add_item(item_id,label,quantity):
    cart=st.session_state.cart
    existing=next((x for x in cart if x.item_id==item_id),None)
    if existing:
        if existing.quantity+quantity>10:
            st.warning('같은 상품은 합계 10개까지 담을 수 있습니다.')
            return False
        st.session_state.cart=[replace(x,quantity=x.quantity+quantity) if x.item_id==item_id else x for x in cart]
        st.session_state[f'qty_{item_id}']=existing.quantity+quantity
    elif len(cart)>=5:
        st.warning('장바구니에는 최대 5종의 상품을 담을 수 있습니다.')
        return False
    else:
        st.session_state.cart=cart+[CartItem(item_id,label,quantity)]
    invalidate()
    return True


def add_card(row, quantity, demo_id=None):
    if demo_id:
        added = add_item(demo_id, row.product_name, quantity)
    else:
        matching = [link for link in st.session_state.links if
                    (link.brand, link.barcode, link.product_code, link.product_name) ==
                    (row.brand, row.barcode, row.product_code, row.product_name)]
        item_id = matching[0].item_id if matching else uuid4().hex
        added = add_item(item_id, row.product_name, quantity)
        if added and not matching:
            st.session_state.links.append(CatalogLink(item_id, row.brand, row.barcode, row.product_code, row.product_name))
    if added:
        st.toast(f'{row.product_name} {quantity}개를 담았습니다.', icon=':material/check:')


if 'manager' not in st.session_state:
    manager,directory=session_manager('LIVE')
    st.session_state.update(manager=manager,temp_directory=directory,cart=[],links=demo_links(),
                            snapshot=None,recommendations=None,active_mode='LIVE',search_rows=[],settings_key=None)
    st.session_state.mode = 'LIVE'
    st.session_state.links = []
    st.session_state.budget = 6000

st.title('편의점 장바구니 v2')
st.write('사진으로 상품을 찾고, 필요한 수량만 담아 보세요.')
st.caption('예산에 맞는 수량 · 두 매장 나눠 구매 · 품절 대체 · 행사 수량 안내')

with st.sidebar:
    st.subheader('내 주변 편의점', icon=':material/storefront:')
    st.caption(f'장바구니 {len(st.session_state.cart)}종 · {sum(item.quantity for item in st.session_state.cart)}개')
    mode=st.radio('데이터 모드',['TEST','LIVE'],format_func=lambda x:'시연용 데이터' if x=='TEST' else '실시간 조회',key='mode')
    with st.form('address_form'):
        address=st.text_input('기준 주소 또는 장소',placeholder='예: 서울대학교 중앙도서관')
        locate=st.form_submit_button('주소 찾기')
    if locate:
        try:
            lat,lng,label=search_korean_address(address)
            st.session_state.latitude=lat;st.session_state.longitude=lng
            st.success(label)
        except Exception as exc:
            st.error(f'주소를 찾지 못했습니다. 좌표를 직접 입력할 수 있습니다. ({exc})')
    lat=st.number_input('기준 위도',min_value=-90.0,max_value=90.0,value=DEFAULT_LAT,format='%.6f',key='latitude')
    lng=st.number_input('기준 경도',min_value=-180.0,max_value=180.0,value=DEFAULT_LNG,format='%.6f',key='longitude')
    st.caption('기본 위치는 서울대학교 인근입니다. 거리는 도보 거리가 아닌 직선거리입니다.')
    brands=st.multiselect('비교 브랜드',BRANDS,default=BRANDS,key='brands')
    radius=st.select_slider('검색 반경 (km)',options=[0.5,1.0,2.0],value=2.0,key='radius')
    budget=st.number_input('예산 (원, 0은 제한 없음)',min_value=0,value=None,step=500,key='budget')

if mode != st.session_state.active_mode:
    st.session_state.temp_directory.cleanup()
    manager,directory=session_manager(mode)
    st.session_state.update(manager=manager,temp_directory=directory,cart=[],links=demo_links() if mode=='TEST' else [],
                            active_mode=mode,search_rows=[],search_statuses=[],searched_keyword=None)
    invalidate()
settings=(mode,lat,lng,tuple(sorted(brands)))
if settings != st.session_state.settings_key:
    invalidate()
    st.session_state.search_rows=[]
    st.session_state.search_statuses=[]
    st.session_state.searched_keyword=None
    st.session_state.settings_key=settings

if mode=='TEST':
    st.info('시연용 가상 상품·매장입니다. 실제 편의점의 재고나 가격을 나타내지 않습니다.')
    with st.container(horizontal=True,vertical_alignment='bottom'):
        st.selectbox('v2 시연 상황',['예산 맞추기','두 매장 비교','품절 대체','행사 수량'],key='v2_demo_scenario',width=220)
        st.button('상황 불러오기',key='v2_load_scenario',on_click=load_v2_scenario,icon=':material/play_arrow:')
    if st.button('예시 장바구니 채우기',key='load_demo',icon=':material/add_shopping_cart:'):
        st.session_state.links=demo_links()
        st.session_state.cart=[CartItem('drink','시연 음료 250ml',2),CartItem('noodle','시연 컵라면 65g',1),CartItem('snack','시연 과자 60g',1)]
        for item in st.session_state.cart:
            st.session_state[f'qty_{item.item_id}']=item.quantity
        invalidate()
else:
    st.caption('CU 공식 사진 목록 · 재고와 최종 결제액은 매장 비교에서 확인하세요.')

with st.expander('상품 목록 · 더 담기',expanded=not bool(st.session_state.cart),icon=':material/storefront:'):
    if mode=='TEST':
        labels={x.item_id:x.product_name for x in st.session_state.links}
        keyword=st.text_input('상품 이름으로 검색',placeholder='예: 음료, 컵라면, 과자',key='catalog_keyword')
        quantity=st.number_input('담을 수량',1,10,1,key='add_quantity')
        rows=st.session_state.manager.test_adapter.search('',lat,lng)
        # Demo identities intentionally span brands; render one card per cart item.
        demo_rows={r.product_name:r for r in reversed(rows) if r.brand in brands}
        products=list_products(list(demo_rows.values()),keyword)
        render_product_cards(products,add_card,quantity,{name:item_id for item_id,name in labels.items()})
    else:
        keyword=st.text_input('상품 이름으로 검색',placeholder='예: 펩시, 환타, 나뚜루 · 목록에 없으면 실시간 검색',key='live_keyword',icon=':material/search:')
        with st.container(horizontal=True, vertical_alignment='bottom'):
            with st.popover('필터',icon=':material/tune:'):
                brand_filter=st.selectbox('목록 브랜드',['전체',*brands],key='catalog_brand')
                sort=st.selectbox('정렬',['기본순','이름순'],key='catalog_sort')
            quantity=st.number_input('담을 수량',1,10,1,key='card_quantity',width=135)
            search=st.button('실시간 상품 검색',key='search_live',type='primary',icon=':material/search:')
        if search:
            if not keyword.strip() or not brands:
                st.warning('상품명과 비교 브랜드를 선택하세요.')
            else:
                try:
                    with st.spinner('선택한 브랜드의 상품과 매장을 조회하고 있습니다.'):
                        rows,statuses=st.session_state.manager.search_all(keyword.strip(),lat,lng,brands,mode,include_unpriced=True)
                    st.session_state.search_rows=rows
                    st.session_state.search_statuses=statuses
                    st.session_state.searched_keyword=keyword.strip()
                except Exception as exc:
                    st.session_state.search_rows=[]
                    st.session_state.search_statuses=[]
                    st.session_state.searched_keyword=keyword.strip()
                    st.error(f'상품을 조회하지 못했습니다. 잠시 후 다시 검색해 주세요. ({exc})')
        queried=bool(keyword.strip()) and st.session_state.get('searched_keyword')==keyword.strip()
        browser_rows=st.session_state.search_rows if queried else [p for p in load_photo_catalog() if p.brand in brands]
        st.caption('실시간 검색 결과 · 외부 서비스가 반환한 상품입니다.' if queried else
                   '저장된 목록에서 검색합니다. 더 찾으려면 실시간 상품 검색을 눌러 주세요.')
        render_product_cards(list_products(browser_rows,keyword,brand_filter,sort),add_card,quantity)
        candidates={}
        for row in st.session_state.search_rows if queried else []:
            key=(row.brand,row.barcode,row.product_code,row.product_name)
            candidates.setdefault(key,row)
        if candidates:
            with st.form('choose_live'):
                chosen=st.multiselect('같은 상품과 규격을 브랜드별로 선택',list(candidates),
                                       format_func=lambda k:f'{k[0]} · {k[3]} · 코드 {k[1] or k[2] or "없음"}',key='live_selection')
                confirmed=st.checkbox('선택한 상품의 용량·맛·구성이 같음을 확인했습니다.',key='confirm_mapping')
                quantity=st.number_input('담을 수량',1,10,1,key='live_quantity')
                add=st.form_submit_button('선택 상품 담기')
            if add:
                selected=[candidates[k] for k in chosen]
                if not selected or (len(selected)>1 and not confirmed):
                    st.warning('상품을 선택하고 여러 브랜드를 묶을 때는 같은 규격인지 확인하세요.')
                elif len({r.brand for r in selected})!=len(selected):
                    st.warning('한 장바구니 항목에는 브랜드별 상품을 하나씩만 선택하세요.')
                else:
                    signature={(r.brand,r.barcode,r.product_code,r.product_name) for r in selected}
                    overlap=[]
                    for item in st.session_state.cart:
                        existing={(x.brand,x.barcode,x.product_code,x.product_name) for x in st.session_state.links if x.item_id==item.item_id}
                        if signature & existing:
                            overlap.append((item,signature==existing))
                    if overlap and (len(overlap)>1 or not overlap[0][1]):
                        st.warning('기존 항목과 일부 상품이 겹칩니다. 해당 항목을 삭제한 뒤 함께 선택하세요.')
                    else:
                        new_id=overlap[0][0].item_id if overlap else uuid4().hex
                        before=len(st.session_state.cart)
                        add_item(new_id,selected[0].product_name,int(quantity))
                        if not overlap and len(st.session_state.cart)>before:
                            st.session_state.links.extend(CatalogLink(new_id,r.brand,r.barcode,r.product_code,r.product_name) for r in selected)
        elif search:
            st.warning('조회된 상품이 없습니다. 아래 조회 상태를 확인하세요.')
        if queried:
            for status in st.session_state.get('search_statuses',[]):
                if status.source == 'ERROR':
                    st.warning(f'{status.brand}: {status.message}')
                else:
                    st.caption(f'{status.brand} · {status.source} · {status.message}')

with st.expander(f'내 장바구니 · {len(st.session_state.cart)}종',expanded=st.session_state.snapshot is None,icon=':material/shopping_basket:'):
    invalid_quantity=False
    for item in list(st.session_state.cart):
        left,middle,right=st.columns([6,2,1],vertical_alignment='bottom')
        left.write(item.label)
        left.checkbox('예산 조정에서 수량 유지',key=f'locked_{item.item_id}',help='예산 도우미가 이 상품을 줄이거나 제외하지 않습니다.')
        qty_key=f'qty_{item.item_id}'
        if qty_key not in st.session_state:
            st.session_state[qty_key]=item.quantity
        value=middle.number_input(f'{item.label} 수량',1,10,value=None,key=qty_key,label_visibility='collapsed')
        if value is None:
            invalid_quantity=True
            invalidate()
            middle.caption('수량을 입력하세요.')
        elif value != item.quantity:
            st.session_state.cart=[replace(x,quantity=int(value)) if x.item_id==item.item_id else x for x in st.session_state.cart]
            invalidate()
        if right.button('삭제',key=f'remove_{item.item_id}'):
            st.session_state.cart=[x for x in st.session_state.cart if x.item_id!=item.item_id]
            invalidate()
            st.rerun()
    if not st.session_state.cart:
        st.info('상품을 담아 주세요. 시연 모드에서는 예시 장바구니로 바로 시작할 수 있습니다.')
        st.stop()

if budget is None:
    st.info('예산을 입력해 주세요. 예산 제한이 없으면 0을 입력합니다.')
    st.stop()

force=st.checkbox('재고 캐시를 건너뛰고 새로 조회',key='force_refresh',help='재고에 적용합니다. 행사 목록은 별도의 6시간 캐시와 원래 자료 시각을 사용합니다.')
if st.button('매장 비교하기',type='primary',key='compare',disabled=not brands or invalid_quantity,icon=':material/compare_arrows:'):
    try:
        with st.spinner('상품별 자료를 확인하고 장바구니 금액을 계산하고 있습니다.'):
            st.session_state.snapshot=fetch_snapshot(st.session_state.cart,st.session_state.links,
                                                   st.session_state.manager,lat,lng,brands,mode,force)
    except Exception as exc:
        invalidate()
        st.error(f'비교를 완료하지 못했습니다: {exc}')
snapshot=st.session_state.snapshot
if snapshot is None:
    st.caption('장바구니나 비교 조건이 변경되면 다시 비교해 주세요.')
    st.stop()

resolved=resolve_cart_records(st.session_state.cart,snapshot.records,st.session_state.links)
all_stores=evaluate_stores(st.session_state.cart,resolved,lat,lng,radius,max_stores=10000)
stores=apply_current_evidence(all_stores[:10])
recommendation=recommend_baskets(stores,int(budget))
st.session_state.recommendations=recommendation
st.subheader('비교 결과')
st.caption(f'비교 완료 {snapshot.completed_at} · 직선거리 {radius:g}km · 대상 {len(stores)}개 매장'
           +(f' · 상한으로 제외 {len(all_stores)-10}곳' if len(all_stores)>10 else ''))
st.write(recommendation.message)
if recommendation.cheapest:
    best=recommendation.cheapest
    a,b,c=st.columns(3)
    a.metric('최저가 매장',best.store_name,f'{best.total:,}원',delta_color='off')
    nearest=recommendation.nearest
    b.metric('가장 가까운 매장',nearest.store_name if nearest else '위치 미확인',
             f'{nearest.distance_km:.2f}km' if nearest else None,delta_color='off')
    c.metric('예산 잔액' if not recommendation.over_budget_by else '예산 초과',
             '제한 없음' if budget==0 else f'{abs(int(budget)-best.total):,}원')
    if recommendation.over_budget_by:
        st.warning('예산 내에서 장바구니 전체를 구매할 수 있는 확인된 매장이 없습니다.')
else:
    st.warning('확정 추천이 없습니다. 매장별 부족·미확인 사유를 확인해 주세요.')

render_v2(stores,st.session_state.cart,int(budget),lat,lng,radius,brands,mode)

with st.expander('매장별 전체 비교와 상세 자료',expanded=False,icon=':material/table_chart:'):
    def won(value):
        return f'{value:,}원' if value is not None else '미확인'

    display=pd.DataFrame([{'매장':s.store_name,'확정 추천 가능':'예' if s.eligible else '아니오',
                           '총액':won(s.total),'확인된 상품 소계':won(s.known_subtotal),'참고 총액':won(s.reference_total),
                           '직선거리':f'{s.distance_km:.2f}km' if s.distance_km is not None else '미확인','자료 상태':s.source,
                           '확인 사항':'; '.join(s.reasons) or '모든 상품 필요 수량과 금액 확인'} for s in stores])
    if not display.empty:
        st.dataframe(display,hide_index=True,width='stretch',alt='매장별 장바구니 가격과 재고 확인 상태 비교표')
        st.download_button('비교 결과 CSV 저장',comparison_csv(stores,snapshot),file_name='장바구니_비교.csv',
                           mime='text/csv',on_click='ignore',icon=':material/download:')
        chosen_store=st.selectbox('매장별 상세 보기',stores,format_func=lambda s:s.store_name,key='store_detail')
        for item,line in zip(st.session_state.cart,chosen_store.lines):
            row=line.record
            with st.container(border=True):
                st.write(f'{item.label} · {item.quantity}개 · {line.reason}')
                if row:
                    promo_label=(row.promotion or '없음') if row.promotion_known else '미확인'
                    st.caption(f'판매가 {won(row.price) if row.price>0 else "미확인"} · 행사 {promo_label} · 재고 {row.stock_display}')
                    st.write(f'상품 결제액 {won(line.payable)} / 참고액 {won(line.reference_payable)}')
                    st.caption(f'가격: {row.price_source or "미확인"} ({row.price_updated_at or row.updated_at})')
                    st.caption(f'재고: {row.stock_source or "미확인"} ({row.stock_updated_at or row.updated_at})')
                    st.caption(f'행사: {row.promotion_source or "미확인"} ({row.promotion_updated_at or row.updated_at})')
        address=next((line.record.address for line in chosen_store.lines if line.record),'')
        if mode=='LIVE':
            st.link_button('매장 위치 검색',f'https://map.naver.com/p/search/{quote(chosen_store.brand+" "+chosen_store.store_name+" "+address)}')
    else:
        st.info('현재 조건에서 표시할 매장 자료가 없습니다.')
    with st.expander('상품별 조회 상태와 계산 기준'):
        for item in st.session_state.cart:
            for status in snapshot.statuses_by_item.get(item.item_id,[]):
                st.write(f'{item.label} · {status.brand} · {status.source}: {status.message}')
        st.caption('수량은 무료 증정분을 포함한 수령 수량입니다. 일반 판매·같은 상품 1+1·2+1만 기본 계산하며 결제수단 할인과 교차 증정은 제외합니다. 방문 전 재고를 재확인하세요.')
