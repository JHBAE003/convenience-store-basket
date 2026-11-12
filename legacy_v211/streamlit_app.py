from dataclasses import replace
from urllib.parse import quote
from uuid import uuid4
import pandas as pd
import streamlit as st
import re
import json
from copy import deepcopy

from core.config import BRANDS, DEFAULT_LAT, DEFAULT_LNG, BASE_DIR
from core.cart_models import CartItem, CatalogLink
from core.models import StoreProduct
from core.services.catalog import (load_catalog, resolve_cart_records, matches_link, load_user_catalog,
                                   save_confirmed_links, reusable_links_for, catalog_link_for)
from core.services.product_matching import shared_gtin, conflicting_mapping_variants
from core.services.query_service import new_session_manager, fetch_snapshot, add_promotion_observations, retry_failed_brands
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
from core.ui.evidence import source_label, render_record_evidence, render_coverage, render_received_observations
from core.security.csv_cells import csv_safe_frame

st.set_page_config(page_title='편의점 장바구니 v2.1.1',page_icon=':material/shopping_basket:',layout='wide')
LIVE_BACKEND_REVISION='v211-security-20261101-1'


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
    st.session_state.promotion_notes=[]


def start_product_linking(item_id):
    """기존 수량을 유지하며 다른 브랜드의 후보만 명시적으로 검색한다."""
    item=next((i for i in st.session_state.cart if i.item_id==item_id),None)
    if item is None or st.session_state.mode!='LIVE':
        return
    keyword=re.sub(r'^[^)]{1,30}\)', '', item.label).strip()
    keyword=re.sub(r'(?:p)?\d+(?:\.\d+)?\s*(?:kg|ml|g|l)\b','',keyword,flags=re.I).strip() or item.label
    st.session_state.mapping_target=item_id
    st.session_state.live_keyword=keyword
    st.session_state.pop('live_selection',None)
    st.session_state.pop('confirm_mapping',None)
    try:
        rows,statuses=st.session_state.manager.search_products(keyword,st.session_state.latitude,
                            st.session_state.longitude,st.session_state.brands,'LIVE')
        st.session_state.search_rows=rows
        st.session_state.search_statuses=statuses
        st.session_state.searched_keyword=keyword
    except Exception:
        st.session_state.search_rows=[]
        st.session_state.search_statuses=[]
        st.session_state.searched_keyword=keyword


def cancel_product_linking():
    st.session_state.mapping_target=None


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
        from core.services.cart_linking import overlaps_active_identity
        active_ids={item.item_id for item in st.session_state.cart}
        matching = [link for link in st.session_state.links
                    if link.item_id in active_ids and matches_link(row,link)]
        overlaps=[link for link in st.session_state.links
                  if link.item_id in active_ids and overlaps_active_identity(row,link)]
        if overlaps and (not matching or len({link.item_id for link in overlaps})>1):
            st.warning('같은 코드의 상품이 이미 담겨 있습니다. 기존 항목의 상품 표기와 수량을 확인해 주세요.')
            return
        item_id = matching[0].item_id if matching else uuid4().hex
        added = add_item(item_id, row.product_name, quantity)
        if added and not matching:
            saved = reusable_links_for(row, st.session_state.get('user_catalog', []))
            if saved:
                saved_rows=[StoreProduct(link.brand,'','',link.product_name,barcode=link.barcode,
                            product_code=link.product_code) for link in saved]
                if any(overlaps_active_identity(candidate,link) for candidate in saved_rows
                       for link in st.session_state.links if link.item_id in active_ids):
                    saved=[]
            if saved:
                st.session_state.links.extend(replace(link, item_id=item_id) for link in saved)
                st.toast('이전에 확인한 같은 상품 연결을 함께 불러왔습니다.')
            else:
                mapped = [row]
                for other in st.session_state.search_rows:
                    already_linked=any(link.item_id in active_ids and overlaps_active_identity(other,link)
                                       for link in st.session_state.links)
                    if (other.brand not in {p.brand for p in mapped}) and shared_gtin(row, other) and not already_linked:
                        mapped.append(other)
                st.session_state.links.extend(catalog_link_for(p, item_id) for p in mapped)
    if added:
        st.toast(f'{row.product_name} {quantity}개를 담았습니다.', icon=':material/check:')


if 'manager' in st.session_state and st.session_state.get('backend_revision')!=LIVE_BACKEND_REVISION:
    previous=st.session_state.manager
    previous_directory=st.session_state.temp_directory
    manager,directory=session_manager(st.session_state.get('active_mode','LIVE'))
    # 코드 재실행으로 오래된 관리자 객체가 남지 않도록 교체한다.
    # 장바구니·선택·이미 받은 응답의 시각은 유지하고 오류 대기만 새로 시작한다.
    for name in ('_last_good','_cache_issues','_product_cache','_store_cache','_region_cache'):
        if hasattr(previous,name):
            setattr(manager,name,deepcopy(getattr(previous,name)))
    manager.metrics=deepcopy(previous.metrics)
    manager.promotion_manager.cache=deepcopy(previous.promotion_manager.cache)
    st.session_state.update(manager=manager,temp_directory=directory,backend_revision=LIVE_BACKEND_REVISION)
    previous_directory.cleanup()

if 'manager' not in st.session_state:
    manager,directory=session_manager('LIVE')
    st.session_state.update(manager=manager,temp_directory=directory,cart=[],links=demo_links(),
                            snapshot=None,recommendations=None,active_mode='LIVE',search_rows=[],settings_key=None)
    st.session_state.mode = 'LIVE'
    st.session_state.links = []
    st.session_state.budget = 6000
    st.session_state.backend_revision=LIVE_BACKEND_REVISION
    try:
        st.session_state.user_catalog = load_user_catalog()
    except (ValueError, OSError):
        st.session_state.user_catalog = []
        st.session_state.user_catalog_error = True

st.title('편의점 장바구니 v2.1.1')
st.write('상품 찾기 → 장바구니 담기 → 매장 비교')
if st.session_state.get('user_catalog_error'):
    st.warning('저장된 상품 연결표를 읽지 못했습니다. 현재 검색과 직접 선택은 계속 사용할 수 있습니다.')

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
            st.error('주소를 찾지 못했습니다. 잠시 후 다시 시도하거나 좌표를 직접 입력해 주세요.')
    with st.expander('좌표 직접 입력',expanded=False):
        lat=st.number_input('기준 위도',min_value=-90.0,max_value=90.0,value=DEFAULT_LAT,format='%.6f',key='latitude')
        lng=st.number_input('기준 경도',min_value=-180.0,max_value=180.0,value=DEFAULT_LNG,format='%.6f',key='longitude')
    st.caption('기본 위치는 서울대학교 인근입니다. 거리는 도보 거리가 아닌 직선거리입니다.')
    brands=st.multiselect('비교 브랜드',BRANDS,default=BRANDS,key='brands')
    radius=st.select_slider('검색 반경 (km)',options=[0.5,1.0,2.0,3.0,5.0,10.0,20.0],value=2.0,key='radius')
    budget=st.number_input('예산 (원, 0은 제한 없음)',min_value=0,value=None,step=500,key='budget')

if mode != st.session_state.active_mode:
    st.session_state.temp_directory.cleanup()
    manager,directory=session_manager(mode)
    st.session_state.update(manager=manager,temp_directory=directory,cart=[],links=demo_links() if mode=='TEST' else [],
                            active_mode=mode,search_rows=[],search_statuses=[],searched_keyword=None,mapping_target=None)
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
        st.selectbox('기능별 시연',['예산 맞추기','두 매장 비교','품절 대체','행사 수량'],key='v2_demo_scenario',width=220)
        st.button('상황 불러오기',key='v2_load_scenario',on_click=load_v2_scenario,icon=':material/play_arrow:')
    if st.button('예시 장바구니 채우기',key='load_demo',icon=':material/add_shopping_cart:'):
        st.session_state.links=demo_links()
        st.session_state.cart=[CartItem('drink','시연 음료 250ml',2),CartItem('noodle','시연 컵라면 65g',1),CartItem('snack','시연 과자 60g',1)]
        for item in st.session_state.cart:
            st.session_state[f'qty_{item.item_id}']=item.quantity
        invalidate()
else:
    st.caption('저장 사진은 CU 상품입니다. 모든 브랜드의 상품을 찾으려면 실시간 상품 검색을 눌러 주세요. 목록 가격은 참고가입니다.')

with st.expander('상품 목록 · 더 담기',expanded=not bool(st.session_state.cart) or bool(st.session_state.get('mapping_target')),icon=':material/storefront:'):
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
                    with st.spinner('선택한 브랜드의 상품 후보를 찾고 있습니다.'):
                        rows,statuses=st.session_state.manager.search_products(keyword.strip(),lat,lng,brands,mode)
                    st.session_state.search_rows=rows
                    st.session_state.search_statuses=statuses
                    st.session_state.searched_keyword=keyword.strip()
                except Exception as exc:
                    st.session_state.search_rows=[]
                    st.session_state.search_statuses=[]
                    st.session_state.searched_keyword=keyword.strip()
                    st.error('상품을 조회하지 못했습니다. 잠시 후 다시 검색해 주세요.')
        queried=bool(keyword.strip()) and st.session_state.get('searched_keyword')==keyword.strip()
        browser_rows=st.session_state.search_rows if queried else [p for p in load_photo_catalog() if p.brand in brands]
        st.caption('상품 검색 결과 · 담은 상품의 매장 재고는 비교할 때 확인합니다.' if queried else
                   '저장된 목록에서 검색합니다. 더 찾으려면 실시간 상품 검색을 눌러 주세요.')
        if queried:
            with st.container(horizontal=True):
                for status in st.session_state.get('search_statuses',[]):
                    st.caption(f'{status.brand} · {source_label(status.source)} · 후보 {len(status.records)}개')
        products=list_products(browser_rows,keyword,brand_filter,sort)
        if brand_filter=='전체' and sort=='기본순' and queried:
            groups={brand:[row for row in products if row.brand==brand] for brand in brands}
            products=[group[index] for index in range(max((len(g) for g in groups.values()),default=0))
                      for group in groups.values() if index<len(group)]
        render_product_cards(products,add_card,quantity)
        candidates={}
        for row in st.session_state.search_rows if queried else []:
            key=(row.brand,row.barcode,row.product_code,row.product_name)
            candidates.setdefault(key,row)
        if candidates:
            target=next((i for i in st.session_state.cart if i.item_id==st.session_state.get('mapping_target')),None)
            if target:
                st.info(f'장바구니의 「{target.label}」에 연결할 같은 상품을 선택하세요. 기존 수량 {target.quantity}개는 유지합니다.')
                st.button('연결 취소',key='cancel_mapping',on_click=cancel_product_linking)
            with st.form('choose_live'):
                chosen=st.multiselect('같은 상품과 규격을 브랜드별로 선택',list(candidates),
                                       format_func=lambda k:f'{k[0]} · {k[3]} · 코드 {k[1] or k[2] or "없음"}',key='live_selection')
                confirmed=st.checkbox('선택한 상품의 용량·맛·구성이 같음을 확인했습니다.',key='confirm_mapping')
                quantity=st.number_input('담을 수량',1,10,1,key='live_quantity')
                add=st.form_submit_button('이 상품에 연결하기' if target else '선택 상품 담기')
            if add:
                selected=[candidates[k] for k in chosen]
                if target:
                    from core.services.cart_linking import attach_confirmed_products
                    try:
                        st.session_state.links=attach_confirmed_products(st.session_state.cart,
                             st.session_state.links,target.item_id,selected,confirmed)
                        invalidate()
                        st.session_state.mapping_target=None
                        new_links=[link for link in st.session_state.links if link.item_id==target.item_id]
                        try:
                            save_confirmed_links(new_links,confirmed=True)
                            st.session_state.user_catalog=load_user_catalog()
                        except (ValueError,OSError):
                            st.warning('이번 장바구니 연결은 적용했지만 다음 사용을 위한 저장은 하지 못했습니다.')
                        st.success('브랜드별 상품을 연결했습니다. 매장 비교하기를 다시 눌러 주세요.')
                    except ValueError as error:
                        st.warning(str(error))
                elif not selected or (len(selected)>1 and not confirmed):
                    st.warning('상품을 선택하고 여러 브랜드를 묶을 때는 같은 규격인지 확인하세요.')
                elif any(conflicting_mapping_variants(a,b) for i,a in enumerate(selected) for b in selected[i+1:]):
                    st.warning('표시된 제조사·용량·맛·포장 구성이 다릅니다. 서로 다른 상품은 각각 담아 주세요.')
                elif len({r.brand for r in selected})!=len(selected):
                    st.warning('한 장바구니 항목에는 브랜드별 상품을 하나씩만 선택하세요.')
                else:
                    from core.services.cart_linking import overlaps_active_identity
                    signature={(r.brand,r.barcode,r.product_code,r.product_name) for r in selected}
                    overlap=[]
                    for item in st.session_state.cart:
                        existing={(x.brand,x.barcode,x.product_code,x.product_name) for x in st.session_state.links if x.item_id==item.item_id}
                        if any(overlaps_active_identity(row,link) for row in selected
                               for link in st.session_state.links if link.item_id==item.item_id):
                            overlap.append((item,signature==existing))
                    if overlap and (len(overlap)>1 or not overlap[0][1]):
                        st.warning('기존 항목과 일부 상품이 겹칩니다. 해당 항목을 삭제한 뒤 함께 선택하세요.')
                    else:
                        new_id=overlap[0][0].item_id if overlap else uuid4().hex
                        before=len(st.session_state.cart)
                        add_item(new_id,selected[0].product_name,int(quantity))
                        if not overlap and len(st.session_state.cart)>before:
                            new_links=[catalog_link_for(r,new_id) for r in selected]
                            st.session_state.links.extend(new_links)
                            if confirmed:
                                try:
                                    save_confirmed_links(new_links,confirmed=True)
                                    st.session_state.user_catalog=load_user_catalog()
                                    st.success('확인한 상품 연결을 이 컴퓨터에 저장했습니다. 다음에 담을 때 다시 사용할 수 있습니다.')
                                except (ValueError,OSError):
                                    st.warning('상품은 담았지만 연결표를 저장하지 못했습니다. 이번 장바구니에서는 사용할 수 있습니다.')
        elif search:
            st.warning('조회된 상품이 없습니다. 아래 조회 상태를 확인하세요.')
        if queried:
            for status in st.session_state.get('search_statuses',[]):
                if status.source == 'ERROR':
                    st.warning(f'{status.brand}: 상품 후보를 확인하지 못했습니다. 잠시 후 다시 검색해 주세요.')

with st.expander(f'내 장바구니 · {len(st.session_state.cart)}종',expanded=st.session_state.snapshot is None,icon=':material/shopping_basket:'):
    invalid_quantity=False
    for item in list(st.session_state.cart):
        left,middle,right=st.columns([6,2,1],vertical_alignment='bottom')
        left.write(item.label)
        if mode=='LIVE':
            linked={link.brand for link in st.session_state.links if link.item_id==item.item_id}
            missing=[brand for brand in brands if brand not in linked]
            left.caption('연결된 브랜드: '+(', '.join(sorted(linked)) or '없음')
                         +(' · 미연결: '+', '.join(missing) if missing else ''))
            left.button('다른 편의점 상품 연결',key=f'link_{item.item_id}',on_click=start_product_linking,args=(item.item_id,))
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

force=st.checkbox('앱에 저장된 재고를 사용하지 않고 다시 조회',key='force_refresh',help='이 앱의 재고 캐시만 건너뜁니다. 중계 서비스나 편의점 원본 자료까지 갱신됐다는 뜻은 아닙니다.')
if st.button('매장 비교하기',type='primary',key='compare',disabled=not brands or invalid_quantity,icon=':material/compare_arrows:'):
    try:
        st.session_state.promotion_notes=[]
        with st.spinner('상품별 자료를 확인하고 장바구니 금액을 계산하고 있습니다.'):
            st.session_state.snapshot=fetch_snapshot(st.session_state.cart,st.session_state.links,
                                                   st.session_state.manager,lat,lng,brands,mode,force)
    except Exception as exc:
        invalidate()
        st.error('비교를 완료하지 못했습니다. 상품 연결과 조회 조건을 확인한 뒤 잠시 후 다시 시도해 주세요.')
snapshot=st.session_state.snapshot
if snapshot is None:
    st.caption('장바구니나 비교 조건이 변경되면 다시 비교해 주세요.')
    st.stop()

if mode=='LIVE':
    if any(s.source in ('ERROR','CACHE') for s in snapshot.statuses):
        if st.button('실패한 브랜드만 다시 조회',key='retry_failed_brands',icon=':material/refresh:'):
            with st.spinner('실패한 브랜드만 다시 확인하고 있습니다. 다른 브랜드의 자료는 유지합니다.'):
                snapshot,retried=retry_failed_brands(snapshot,st.session_state.cart,st.session_state.links,
                        st.session_state.manager,lat,lng,brands,mode)
                st.session_state.snapshot=snapshot
                st.caption(', '.join(retried)+' 재조회 · 다른 브랜드는 이전 응답과 시각을 유지합니다.')
    st.caption('재고를 먼저 표시합니다. 공식 행사 전체 목록은 별도 확인하며, 행사 미확인을 행사 없음으로 계산하지 않습니다.')
    if st.button('공식 행사 정보 추가 확인',key='check_promotions',icon=':material/event:'):
        with st.spinner('재고를 다시 요청하지 않고 행사 정보를 추가로 확인하고 있습니다.'):
            snapshot,notes=add_promotion_observations(snapshot,st.session_state.manager)
            st.session_state.snapshot=snapshot
            st.session_state.promotion_notes=notes
    for note in st.session_state.get('promotion_notes',[]):
        st.caption(note)

resolved=resolve_cart_records(st.session_state.cart,snapshot.records,st.session_state.links)
all_stores=evaluate_stores(st.session_state.cart,resolved,lat,lng,radius,max_stores=10000)
stores=apply_current_evidence(all_stores[:10])
recommendation=recommend_baskets(stores,int(budget))
st.session_state.recommendations=recommendation
st.subheader('비교 결과')
render_coverage(snapshot)
render_received_observations(snapshot,lat,lng,radius)
st.caption(f'직선거리 {radius:g}km · 대상 {len(stores)}개 매장'
           +(f' · 상한으로 제외 {len(all_stores)-10}곳' if len(all_stores)>10 else ''))
st.write('확정 구매 추천 · '+recommendation.message)
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
                           '직선거리':f'{s.distance_km:.2f}km' if s.distance_km is not None else '미확인','자료 상태':source_label(s.source),
                           '확인 사항':'; '.join(s.reasons) or '모든 상품 필요 수량과 금액 확인'} for s in stores])
    if not display.empty:
        st.dataframe(csv_safe_frame(display),hide_index=True,width='stretch',alt='매장별 장바구니 가격과 재고 확인 상태 비교표')
        st.download_button('비교 결과 CSV 저장',comparison_csv(stores,snapshot),file_name='장바구니_비교.csv',
                           mime='text/csv',on_click='ignore',icon=':material/download:')
        current_stores={store.store_key:store for store in stores}
        chosen_key=st.selectbox('매장별 상세 보기',list(current_stores),
                                format_func=lambda key:current_stores[key].store_name,key='store_detail_key')
        chosen_store=current_stores[chosen_key]
        for item,line in zip(st.session_state.cart,chosen_store.lines):
            row=line.record
            with st.container(border=True):
                st.write(f'{item.label} · {item.quantity}개 · {line.reason}')
                if row:
                    promo_label=(row.promotion or '없음') if row.promotion_known else '미확인'
                    price_label='판매가' if row.price_data_status in ('TEST','LIVE') else '참고 판매가'
                    st.caption(f'{price_label} {won(row.price) if row.price>0 else "미확인"} · 행사 {promo_label} · 재고 {row.stock_display}')
                    st.write(f'상품 결제액 {won(line.payable)} / 참고액 {won(line.reference_payable)}')
                    render_record_evidence(row)
        address=next((line.record.address for line in chosen_store.lines if line.record),'')
        if mode=='LIVE':
            st.link_button('매장 위치 검색',f'https://map.naver.com/p/search/{quote(chosen_store.brand+" "+chosen_store.store_name+" "+address)}')
    else:
        st.info('현재 조건에서 표시할 매장 자료가 없습니다.')
    with st.expander('상품별 조회 상태와 계산 기준'):
        for item in st.session_state.cart:
            for status in snapshot.statuses_by_item.get(item.item_id,[]):
                st.write(f'{item.label} · {status.brand or "선택 브랜드"} · {source_label(status.source)}')
                if status.source=='ERROR':
                    st.caption('이번 조건의 자료를 확인하지 못했습니다. 상품 연결과 위치를 확인한 뒤 다시 조회해 주세요.')
                elif status.source in ('PARTIAL','CACHE'):
                    st.caption('일부 누락되었거나 이전에 저장한 자료가 포함됩니다. 현재 구매 가능 여부를 확정하지 않습니다.')
        st.caption('수량은 무료 증정분을 포함한 수령 수량입니다. 일반 판매·같은 상품 1+1·2+1만 기본 계산하며 결제수단 할인과 교차 증정은 제외합니다. 방문 전 재고를 재확인하세요.')
    with st.expander('조회 점검 기록'):
        failures=[{'brand':s.brand,'status':s.source,'message':s.message,'details':s.error_details}
                  for s in snapshot.statuses if s.error_details or s.source=='ERROR']
        if failures:
            st.download_button('조회 실패 원인 저장',json.dumps(failures,ensure_ascii=False,indent=2).encode('utf-8'),
                               file_name='조회_실패_원인.json',mime='application/json',on_click='ignore')
        metrics=st.session_state.manager.metrics
        if metrics:
            labels={'brand':'브랜드','path':'조회 구분','request_count':'HTTP 요청 수',
                    'elapsed_ms':'응답 시간(ms)','success':'성공','fallback_used':'보조 경로 사용','error_type':'오류 종류'}
            frame=csv_safe_frame(pd.DataFrame(metrics).rename(columns=labels))
            st.dataframe(frame,hide_index=True,width='stretch',alt='이 세션의 조회 횟수와 응답 시간 기록')
            st.download_button('조회 기록 CSV 저장',frame.to_csv(index=False).encode('utf-8-sig'),
                               file_name='조회_점검_기록.csv',mime='text/csv',on_click='ignore')
            st.caption('이 세션의 최근 200건입니다. 검색어·위치·인증정보는 포함하지 않습니다. 상품·매장·재고 경로별 기록이며 행사 HTTP 요청 수는 포함하지 않습니다.')
        else:
            st.caption('이 세션에서 외부 재고 서비스를 요청한 기록이 없습니다. 시연 자료와 저장 자료만 사용한 경우에도 새 요청은 기록되지 않습니다.')
