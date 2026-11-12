"""기존 TEST를 보존하고 LIVE 공공자료 참고 조회를 더한 v3.2.0."""
import csv
import io
from pathlib import Path
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

from planner.engine import MODES, Product, Settings, cost, promotion_state, solve, rank
from planner.presentation import comparison_text, route_svg
from planner.product_art import icon_path
from planner.brand_summary import brand_offer_rows,observation_rows
from planner.walk_map import ORIGIN, get_network, walking_svg, route_gpx
from planner.providers import (BRANDS, TestProvider, LiveProvider, Snapshot, query_context,
                               korean_today, link_conflict, calculation_scope, group_search_candidates)
from legacy_v211.core.security.csv_cells import csv_cell

st.set_page_config(page_title='편의점 장바구니 v3.2.0',page_icon=':material/shopping_basket:',layout='wide')


def context_for(products, links):
    return query_context(st.session_state['data_mode'], products, links,
                         (st.session_state['origin_lat'],st.session_state['origin_lng']),
                         st.session_state['brands'])


def initialize():
    st.session_state.update(search_keyword='',confirm_link=False,force_refresh=False,dataset_revision='sqlite24-map-v311',search_ui_revision='brand-cards-v1')
    test=st.session_state['data_mode']=='TEST'
    provider=TestProvider() if test else LiveProvider()
    if test:
        products,links,origin=provider.defaults()
        defaults=Settings(origin=origin,radius_km=2.0)
    else:
        products,links=(),{}
        defaults=Settings()
    st.session_state['distance_basis']='도보 경로' if test else '직선 거리 (참고)'
    for key in ('budget','strict_budget','max_stores','radius_km','route_limit_km','return_home','distance_weight','visit_weight'):
        st.session_state[key]=getattr(defaults,key)
    st.session_state.update(products=list(products),links=links,_provider=provider,snapshot=None,search_result=None,
                            origin_lat=defaults.origin[0],origin_lng=defaults.origin[1],brands=list(BRANDS),mode=MODES[0])
    if test:
        for p in products:st.session_state['qty_'+p.id]=1
        st.session_state['snapshot']=provider.lookup(products,links,defaults.origin,BRANDS,context_for(products,links))


def switch_data_mode():
    # 전환 전의 상품·좌표·결과·클라이언트·임시 캐시를 다음 모드로 넘기지 않는다.
    for key in list(st.session_state):
        if key!='data_mode':del st.session_state[key]
    initialize()


def apply_quantity(pid,quantity):st.session_state['qty_'+pid]=quantity


def reset_confirmation():st.session_state['confirm_link']=False


def reset_gallery_page():st.session_state['catalog_page']=1


def add_from_gallery(product):
    origin=(st.session_state['origin_lat'],st.session_state['origin_lng'])
    brands=st.session_state['brands']
    result=st.session_state['_provider'].search(product['name'],origin,brands)
    candidate=next((c for c in result.candidates if c.seed_id==product['id']),None)
    if candidate is None:
        st.session_state['add_message']='비교할 브랜드를 선택한 뒤 다시 담아 주세요.';return
    st.session_state.update(search_result=result,search_context=(origin,tuple(brands),product['name']),
                            search_keyword=product['name'],confirm_link=False)
    add_candidate(candidate.id)


def add_candidate(candidate_id,target='__new__'):
    result=st.session_state.get('search_result')
    candidate=next((c for c in result.candidates if c.id==candidate_id),None) if result else None
    if candidate is None:return
    products=st.session_state['products'];links=st.session_state['links']
    existing_pid=next((pid for pid,cs in links.items() if any(c.id==candidate.id for c in cs)),None)
    if existing_pid:
        st.session_state['qty_'+existing_pid]=max(1,st.session_state.get('qty_'+existing_pid,0))
        st.session_state['add_message']='이미 담은 상품입니다. 장바구니에서 수량을 변경할 수 있습니다.'
        return
    if target!='__new__':
        if not st.session_state.get('confirm_link'):
            st.session_state['add_message']='용량·맛·포장 수가 같은 상품인지 확인한 뒤 체크해 주세요.';return
        if any(c.brand==candidate.brand for c in links.get(target,())):
            st.session_state['add_message']='한 상품에는 브랜드별 후보 하나만 연결합니다. 다른 후보는 새 상품으로 담아 주세요.';return
        if any(link_conflict(c,candidate) for c in links.get(target,())):
            st.session_state['add_message']='용량·맛·포장 정보가 충돌해 같은 상품으로 연결할 수 없습니다.';return
        links[target].append(candidate)
        st.session_state['qty_'+target]=max(1,st.session_state.get('qty_'+target,0))
    else:
        pid=candidate.seed_id or candidate.id
        if not any(p.id==pid for p in products):
            if len(products)>=6:
                st.session_state['add_message']='상품은 최대 6종입니다. 장바구니를 비운 뒤 다시 담아 주세요.';return
            products.append(Product(pid,candidate.name,candidate.spec));links[pid]=[]
        # 같은 합성 상품 ID를 제공하는 파일 데이터는 브랜드가 달라도 같은 규격임을 안다.
        # LIVE 후보는 이 ID가 없으므로 기존 사용자 확인 절차를 사용한다.
        matched=[c for c in result.candidates if c.seed_id==candidate.seed_id and c.spec==candidate.spec] if candidate.seed_id else [candidate]
        for c in matched:
            if not any(old.brand==c.brand for old in links[pid]):links[pid].append(c)
        st.session_state['qty_'+pid]=max(1,st.session_state.get('qty_'+pid,0))
    st.session_state['snapshot']=None
    st.session_state['add_message']='장바구니에 반영했습니다. 가격·재고 확인 버튼으로 조회해 주세요.'


def link_selected_candidate():
    add_candidate(st.session_state['manual_candidate_id'],st.session_state['manual_link_target'])


def clear_cart():
    for p in st.session_state['products']:st.session_state.pop('qty_'+p.id,None)
    st.session_state.update(products=[],links={},snapshot=None)
    st.session_state.pop('manual_link_target',None)


@st.cache_data(show_spinner=False,ttl=300,max_entries=32)
def compare_plans(products,stores,offers,cart,settings,distance_matrix=None):
    return solve(products,stores,offers,cart,settings,distance_matrix)


def short_name(name):return name.removeprefix('[가상] ')


def item_spec(spec,pid):
    parts=spec.split(' · ')
    return ' · '.join((parts[0],parts[-1])) if pid and pid.startswith('TEST-P') and len(parts)==3 else spec


def open_comparison():
    st.session_state['workspace_view']='추천·경로'
    st.session_state['compare_requested']=True


def reset_plan_choice():st.session_state.pop('plan_choice',None)


def clear_search():
    st.session_state.update(search_keyword='',search_result=None,search_context=None,catalog_category='전체',catalog_page=1,confirm_link=False)


def remove_product(pid):
    st.session_state['products']=[p for p in st.session_state['products'] if p.id!=pid]
    st.session_state['links'].pop(pid,None)
    st.session_state.pop('qty_'+pid,None)
    st.session_state.pop('manual_link_target',None)
    st.session_state['snapshot']=None


def product_card(name,spec,pid,price,button_key,callback,args,in_cart=False):
    with st.container(border=True,key='product-card-'+button_key.replace(':','-')):
        st.image(str(icon_path(pid)),alt=name+' 구분용 그림',width=138)
        st.markdown('**'+short_name(name)+'**')
        st.caption(item_spec(spec,pid))
        st.markdown(('**'+f'{price:,}원'+'**' if price else '**가격 미확인**')+' · 참고가')
        st.button('담김' if in_cart else '담기',key=button_key,on_click=callback,args=args,
                  disabled=in_cart,width='stretch',icon=':material/check:' if in_cart else ':material/add:')


st.session_state.setdefault('data_mode','TEST')
if st.session_state.get('dataset_revision')!='sqlite24-map-v311':switch_data_mode()
data_mode=st.session_state['data_mode']
if st.session_state.get('search_ui_revision')!='brand-cards-v1':
    st.session_state.update(search_result=None,search_ui_revision='brand-cards-v1',confirm_link=False)
    if data_mode=='TEST':st.session_state['_provider']=TestProvider()
st.session_state.setdefault('workspace_view','상품 찾기')
provider=st.session_state['_provider']
st.html(Path(__file__).parent/'assets/app.css')

with st.sidebar:
    st.header('비교 조건')
    st.radio('데이터 모드',('TEST','LIVE'),key='data_mode',on_change=switch_data_mode,horizontal=True,
             help='TEST는 가상 데이터입니다. 모드를 바꾸면 장바구니와 조회 결과를 초기화합니다.')
    st.multiselect('비교 브랜드',BRANDS,key='brands')
    st.selectbox('추천 기준',MODES,key='mode',on_change=reset_plan_choice)
    st.number_input('예산 (원)',min_value=0,max_value=1000000,step=100,key='budget')
    st.slider('최대 방문 매장 수',1,3,key='max_stores')
    with st.expander('이동·세부 조건'):
        st.checkbox('예산 엄격하게 적용',key='strict_budget',help='예산 내 구매는 항상 예산 상한을 적용합니다.')
        st.number_input('출발지에서 각 매장까지 반경 (km)',min_value=0.0,max_value=30.0,step=0.1,key='radius_km')
        st.radio('거리 계산',('도보 경로','직선 거리 (참고)'),key='distance_basis',
                 help='도보 지도는 서울대입구역 주변을 포함합니다. 점포 진입 구간은 추정 거리입니다.')
        st.number_input('전체 이동거리 상한 (km)',min_value=0.0,max_value=100.0,step=0.1,key='route_limit_km')
        st.checkbox('출발지로 돌아오기',key='return_home')
        st.number_input('출발 위도',min_value=-90.0,max_value=90.0,format='%.5f',key='origin_lat')
        st.number_input('출발 경도',min_value=-180.0,max_value=180.0,format='%.5f',key='origin_lng',
                        help='LIVE 검색·조회 시 이 출발 좌표를 사용합니다.')
        st.number_input('거리 부담값 (원/km)',min_value=0,max_value=100000,step=100,key='distance_weight',
                        help='균형 추천의 비교용 값이며 교통비가 아닙니다.')
        st.number_input('추가 방문 부담값 (원/곳)',min_value=0,max_value=100000,step=100,key='visit_weight')
    if data_mode=='TEST':
        with st.expander('브랜드별 TEST 데이터와 출처'):
            locations=provider.dataset.stores();source_rows=[]
            for brand in BRANDS:
                points=[r for r in locations if r['brand']==brand]
                source_rows.append({'브랜드':brand,'포함 점포 수':len(points),'공통 가상 상품 수':300,
                    '점포 위치 출처':', '.join(sorted({r['source_kind'] for r in points})),
                    '확인일':', '.join(sorted({r['checked_at'][:10] for r in points})),
                    '대표 출처 링크':points[0]['source_url'] if points else ''})
            st.dataframe(source_rows,hide_index=True,width='stretch')
            st.caption('점포명·위치만 공개자료 기반입니다. 상품·가격·행사·재고는 가상이며 현재 영업 여부를 보장하지 않습니다.')
            st.caption('그림은 상품 구분용입니다. 행사 기준일: '+provider.metadata['fixed_date'])
    st.caption('컴퓨팅 실습 기말 과제 · v3.2.0')

with st.container(key='app_header',horizontal=True,horizontal_alignment='distribute',vertical_alignment='center'):
    st.title('편의점 장바구니')
    st.badge('TEST · 가상 데이터' if data_mode=='TEST' else 'LIVE · 확인 범위 비교',
             color='orange' if data_mode=='TEST' else 'green')
with st.container(key='workspace_nav'):
    view=st.segmented_control('작업 화면',('상품 찾기','장바구니','추천·경로'),key='workspace_view',required=True,
                              label_visibility='collapsed')

products=st.session_state['products'];links=st.session_state['links']
if data_mode=='LIVE':
    from planner.public_data_ui import render_public_data
    render_public_data((st.session_state['origin_lat'],st.session_state['origin_lng']),
                       st.session_state['brands'], products)
with st.container(key='view-'+{'상품 찾기':'search','장바구니':'cart','추천·경로':'recommendations'}[view]):
    with st.container(key='shopping_workspace'):
        workspace,cart_column=st.columns([1.75,1],gap='medium')
with cart_column:
    with st.container(border=True,key='cart_panel'):
        st.subheader('내 장바구니')
        st.caption(f'{len(products)}종 / 최대 6종')
        cart_summary=st.empty()
        st.button('구매안 비교',key='lookup_button',on_click=open_comparison,type='primary',width='stretch',
                  disabled=not any(st.session_state.get('qty_'+p.id,0)>0 for p in products) or not st.session_state['brands'])
        if not products:st.caption('마음에 드는 상품을 담아 주세요.')
        else:
            for p in products:
                with st.container(horizontal=True,wrap=False,gap='small',key='cart-item-'+p.id):
                    with st.container(width=44):
                        st.image(str(icon_path(p.id)),alt=p.name+' 구분용 그림',width=44)
                    with st.container(width='stretch'):
                        st.markdown('**'+short_name(p.name)+'**')
                        st.caption(item_spec(p.spec,p.id))
                        with st.container(horizontal=True,wrap=False,gap='small',vertical_alignment='center'):
                            st.number_input(f'{p.name} 받을 수량',min_value=0,max_value=12,step=1,key='qty_'+p.id,
                                            label_visibility='collapsed',width=112,help='증정분을 포함해 받을 개수입니다. 0개는 비교에서 제외합니다.')
                            st.button('삭제',key='remove_'+p.id,on_click=remove_product,args=(p.id,),width=52)
            st.button('모두 비우기',key='clear_cart',on_click=clear_cart,type='tertiary')
        with st.popover('조회 옵션',icon=':material/tune:',width='stretch'):
            st.checkbox('저장 결과를 건너뛰고 다시 요청',key='force_refresh')
            st.caption('앱 캐시만 건너뜁니다. 외부 서비스의 원본 갱신을 보장하지 않습니다.')

origin=(st.session_state['origin_lat'],st.session_state['origin_lng']);brands=st.session_state['brands']
cart={p.id:st.session_state.get('qty_'+p.id,0) for p in products if st.session_state.get('qty_'+p.id,0)>0}
active_products=tuple(p for p in products if p.id in cart)
active_links={pid:cs for pid,cs in links.items() if pid in cart}
product_byid={p.id:p for p in products};context=context_for(active_products,active_links)
mode=st.session_state['mode']
settings=Settings(origin=origin,brands=tuple(brands),max_stores=st.session_state['max_stores'],
    radius_km=st.session_state['radius_km'],route_limit_km=st.session_state['route_limit_km'],
    return_home=st.session_state['return_home'],budget=st.session_state['budget'],
    strict_budget=st.session_state['strict_budget'] or mode==MODES[4],
    distance_weight=st.session_state['distance_weight'],visit_weight=st.session_state['visit_weight'],
    as_of=provider.metadata['fixed_date'] if data_mode=='TEST' else korean_today())
snapshot=st.session_state.get('snapshot')
if st.session_state.pop('compare_requested',False):
    with workspace:
        with st.spinner('가격·재고와 구매안을 확인하고 있습니다.'):
            snapshot=provider.lookup(active_products,active_links,origin,brands,context,force=st.session_state['force_refresh'])
            st.session_state['snapshot']=snapshot
received_snapshot=snapshot;issues=[]
if snapshot and not snapshot.usable(context):
    issues.append('조건이 바뀌었거나 확인 자료가 만료됐습니다. 구매안 비교를 다시 눌러 주세요.')
    snapshot=Snapshot(context=context)
elif snapshot is None:snapshot=Snapshot(context=context)
store_byid={s.id:s for s in snapshot.stores}
calculation_stores,calculation_offers,scope_issue=calculation_scope(snapshot,settings)
if scope_issue:issues.append(scope_issue)
walking=st.session_state['distance_basis']=='도보 경로'
distance_label='도보 이동' if walking else '직선 이동'
network=None;distance_matrix=None
if walking:
    distance_matrix=()
    if calculation_stores and cart:
        try:
            points=((ORIGIN,settings.origin),)+tuple((s.id,(s.lat,s.lng)) for s in calculation_stores)
            network=get_network(tuple(sorted(points)));distance_matrix=network.matrix()
            if network.issues:
                issues.append('지도 연결 불가: '+' / '.join(('출발지' if k==ORIGIN else store_byid[k].name)+': '+v for k,v in network.issues.items()))
            unreachable=[s.name for s in calculation_stores if network.distance(ORIGIN,s.id)==float('inf') and s.id not in network.issues]
            if unreachable:issues.append('도보 경로를 찾지 못한 매장: '+', '.join(unreachable))
        except (OSError,ValueError,KeyError):
            issues.append('도보 지도를 사용할 수 없습니다. 지도 파일을 확인하거나 이동·세부 조건에서 직선 참고를 선택해 주세요.')
result=compare_plans(active_products,calculation_stores,calculation_offers,cart,settings,distance_matrix)
st.session_state['last_distance_basis']=st.session_state['distance_basis']
st.session_state['last_summary']={m:({'total':p.total,'route':p.route,'distance':p.distance_km} if p else None) for m,p in result.winners.items()}
st.session_state['last_plan_count']=len(result.plans)
chosen=result.winners[mode]
with cart_summary.container():
    if chosen:
        st.markdown(f'선택 기준 결제액 **{chosen.total:,}원**')
        st.caption(f'{sum(cart.values())}개 · {chosen.count}곳 방문')
    elif cart:st.caption('상품을 담거나 조건을 바꿨다면 구매안을 다시 비교하세요.')


def describe(plan):return ' → '.join(store_byid[k].name for k in plan.route)


def purchase_rows(plan):
    return [{'매장':store_byid[line.store_id].name,'상품':product_byid[line.product_id].name,
             '규격':product_byid[line.product_id].spec,'받을 수량':line.quantity,'단가 (원)':line.unit_price,
             '적용 행사':line.promotion or '일반 판매','결제액 (원)':line.amount,'근거':line.note} for line in plan.lines]


with workspace:
    message=st.session_state.pop('add_message',None)
    if message:
        if message.startswith(('장바구니에 반영','이미 담은')):st.toast('장바구니에 반영했습니다.')
        else:st.warning(message)
    for issue in issues:st.warning(issue)
    if view=='상품 찾기':
        saved_context=st.session_state.get('search_context')
        st.session_state.setdefault('search_keyword',saved_context[2] if saved_context else '')
        with st.form('product_search',border=False):
            search_input,search_action=st.columns([4,1],gap='small',vertical_alignment='bottom')
            search_input.text_input('상품 이름',key='search_keyword',max_chars=80,placeholder='상품명으로 검색 · 우유, 과자, 커피',label_visibility='collapsed',persist_state='session')
            searched=search_action.form_submit_button('검색',key='search_button',width='stretch')
        if searched:
            reset_confirmation();keyword=st.session_state['search_keyword'].strip()
            with st.spinner('상품을 찾고 있습니다.'):
                st.session_state['search_result']=provider.search(keyword,origin,brands) if keyword else None
                st.session_state['search_context']=(origin,tuple(brands),keyword)
            if not keyword:st.warning('상품 이름을 입력해 주세요.')
        search=st.session_state.get('search_result');search_context=st.session_state.get('search_context')
        valid_search=search is not None and search_context and search_context[:2]==(origin,tuple(brands))
        if valid_search:
            st.button('전체 상품으로 돌아가기',key='clear_search',on_click=clear_search,type='tertiary')
            if data_mode=='LIVE':
                for notice in search.messages:st.caption(notice)
            if search.candidates:
                grouped={brand:group_search_candidates(c for c in search.candidates if c.brand==brand) for brand in brands}
                st.caption('참고가 · 점포 확정 판매가와 다를 수 있습니다.')
                for tab,(brand,groups) in zip(st.tabs([f'{brand} · {len(groups)}종' for brand,groups in grouped.items()]),grouped.items()):
                    with tab:
                        if not groups:st.caption('받은 검색 결과가 없습니다.')
                        for offset in range(0,len(groups),3):
                            cards=st.columns(3,gap='small')
                            for column,group in zip(cards,groups[offset:offset+3]):
                                c=group[0];prices=[x.reference_price for x in group if x.reference_price and x.reference_price>0]
                                existing=next((pid for pid,cs in links.items() if any(x.id==c.id for x in cs)),None)
                                in_cart=existing is not None and st.session_state.get('qty_'+existing,0)>0
                                with column:product_card(c.name,c.spec,c.seed_id,min(prices) if prices else None,
                                    'search_add_'+c.id,add_candidate,(c.id,),in_cart)
                if data_mode=='LIVE' and products:
                    with st.expander('다른 브랜드의 같은 상품 연결 (선택)'):
                        by_id={c.id:c for c in search.candidates};targets={p.id:p.name+' · '+p.spec for p in products}
                        st.selectbox('연결할 검색 상품',list(by_id),key='manual_candidate_id',
                            format_func=lambda id:by_id[id].brand+' · '+by_id[id].name+' · '+by_id[id].spec,on_change=reset_confirmation)
                        st.selectbox('연결할 장바구니 상품',list(targets),key='manual_link_target',format_func=targets.get,on_change=reset_confirmation)
                        st.checkbox('기존 상품과 용량·맛·포장 수가 같은 것을 확인했습니다',key='confirm_link')
                        st.button('같은 상품으로 연결',key='manual_link',on_click=link_selected_candidate,disabled=not st.session_state.get('confirm_link'))
            else:st.info('상품을 찾지 못했습니다. 다른 검색어로 다시 확인해 주세요.')
        elif search is not None:
            st.info('브랜드나 위치가 바뀌었습니다. 다시 검색해 주세요.')
        elif data_mode=='TEST':
            catalog=provider.dataset.catalog()
            filters,pagination=st.columns([2,1],gap='small')
            category=filters.selectbox('상품 분류',('전체',)+tuple(dict.fromkeys(p['category'] for p in catalog)),key='catalog_category',on_change=reset_gallery_page,persist_state='session')
            visible=[p for p in catalog if category=='전체' or p['category']==category]
            # 첫 화면에서 여러 분류를 둘러볼 수 있게 섞되 각 분류의 순서는 유지한다.
            if category=='전체':visible=sorted(visible,key=lambda p:((int(p['id'][-3:])-1)%25,(int(p['id'][-3:])-1)//25))
            pages=(len(visible)+11)//12
            page=pagination.selectbox('목록 페이지',range(1,pages+1),key='catalog_page',format_func=lambda p:f'{p} / {pages}',persist_state='session')
            st.caption(f'{len(visible)}종 · 상품 구분용 그림 · 참고가')
            for offset in range((page-1)*12,min(page*12,len(visible)),3):
                cards=st.columns(3,gap='small')
                for column,p in zip(cards,visible[offset:min(offset+3,page*12)]):
                    with column:product_card(p['name'],p['spec'],p['id'],p['base_price'],'catalog_add_'+p['id'],
                        add_from_gallery,(p,),p['id'] in cart)
        else:st.caption('상품 이름을 검색해 시작하세요.')
    elif view=='추천·경로':
        representatives={}
        for label,plan in result.winners.items():
            if plan:representatives.setdefault(plan.signature,[plan,[]])[1].append(label)
        for n in (2,3):
            plan=min((p for p in result.plans if p.count==n),key=lambda p:rank(p,MODES[0]),default=None)
            if plan:representatives.setdefault(plan.signature,[plan,[]])[1].append(f'{n}곳 최저 결제액')
        entries=list(representatives.values());active=None
        if chosen is None and entries:
            st.warning(f'「{mode}」 조건을 만족하는 구매안이 없습니다. 아래에서 다른 구매안을 확인할 수 있습니다.')
        if entries:
            options=[p.signature for p,_ in entries];default=options.index(chosen.signature) if chosen else 0
            if st.session_state.get('plan_choice') not in options:st.session_state.pop('plan_choice',None)
            selected=st.selectbox('구매안',options,index=default,key='plan_choice',
                format_func=lambda sig:next(f'{p.total:,}원 · {p.count}곳 · {p.distance_km*1000:.0f}m / '+' · '.join(labels) for p,labels in entries if p.signature==sig),persist_state='session')
            active,labels=next((p,labels) for p,labels in entries if p.signature==selected)
            with st.container(border=True,key='recommendation_summary'):
                st.caption(' · '.join(labels))
                a,b=st.columns(2)
                a.metric('상품 결제액',f'{active.total:,}원')
                b.metric(distance_label,f'{active.distance_km*1000:,.0f}m',help='도보 거리는 점포 위치와 보행로 사이의 추정 연결을 포함합니다.' if walking else '좌표 사이의 직선 거리입니다.')
                balance=settings.budget-active.total
                st.caption(f'{active.count}곳 방문 · {sum(cart.values())}개 구매 · '+(f'예산 잔액 {balance:,}원' if balance>=0 else f'예산 초과 {-balance:,}원'))
            with st.expander('다른 구매안 비교'):
                st.dataframe([{'추천 기준':' · '.join(labels),'결제액 (원)':p.total,distance_label+' (m)':round(p.distance_km*1000),'방문 수':p.count} for p,labels in entries],hide_index=True,width='stretch')
                other=next((p for p,_ in entries if p.signature!=active.signature),None)
                if other:st.caption(comparison_text(active,other,distance_label))
        else:
            st.warning('조건을 만족하는 전체 구매안이 없습니다.')
            for reason in result.reasons:st.caption(reason)
            if data_mode=='LIVE':st.caption('가격·재고 미확인은 품절이나 재고 0을 뜻하지 않습니다.')
            for pid,missing in result.shortages.items():
                if missing:st.caption(f'{short_name(product_byid[pid].name)}: 확인된 구매 가능 수량이 {missing}개 부족합니다.')
        if active:
            st.subheader('방문 경로')
            st.caption('출발지 → '+describe(active)+(' → 출발지' if settings.return_home else ''))
            if walking and network:
                svg=walking_svg(network,active.route,settings.return_home)
                st.image(svg,alt='실제 거리와 보행로 위에 출발지와 방문 순서를 표시한 도보 경로 지도',width='stretch')
                st.caption('점선은 진입 추정 구간입니다. 현장 통행 가능 여부는 확인이 필요합니다. [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)')
                with st.expander('지도 정보·저장'):
                    downloaded=datetime.fromisoformat(network.base.metadata['downloaded_at']).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
                    st.caption('내려받은 시각: '+downloaded+' (한국 시간) · 원본 갱신 시각: 미제공 · ODbL 1.0')
                    left,right=st.columns(2)
                    left.download_button('지도 SVG',svg.encode('utf-8'),'도보경로.svg','image/svg+xml')
                    right.download_button('도로 GPX',route_gpx(network,active.route,settings.return_home).encode('utf-8'),'도보경로.gpx','application/gpx+xml',help='추정 진입 연결을 제외한 도로 구간만 저장합니다.')
            else:
                st.image(route_svg(active,snapshot.stores,settings.origin,settings.return_home),alt='실제 길과 다른 직선 참고 지도',width='stretch')
                st.caption('실제 도로와 다른 직선 참고 지도입니다.')
            st.subheader('매장별 구매 목록')
            for i,sid in enumerate(active.route,1):
                rows=[r for r,line in zip(purchase_rows(active),active.lines) if line.store_id==sid]
                with st.container(border=True):
                    st.markdown(f'**{i}. {store_byid[sid].name}** · {sum(r["결제액 (원)"] for r in rows):,}원')
                    st.dataframe([{'상품':short_name(r['상품']),'규격':r['규격'],'수량':r['받을 수량'],'행사':r['적용 행사'],'결제액 (원)':r['결제액 (원)']} for r in rows],hide_index=True,width='stretch')
            stream=io.StringIO();rows=purchase_rows(active)
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader()
            writer.writerows({k:csv_cell(v) for k,v in row.items()} for row in rows)
            st.download_button('구매 목록 저장',stream.getvalue().encode('utf-8-sig'),f'구매계획_{data_mode}.csv','text/csv',icon=':material/download:')
        elif result.partial:
            st.subheader('일부 수량 구매 참고안')
            st.caption('전체 수량을 충족하지 못합니다. 일부 구매 예시이며 최대 확보 수량을 보장하지 않습니다.')
            st.dataframe(purchase_rows(result.partial),hide_index=True,width='stretch')
            delivered={k:sum(x.quantity for x in result.partial.lines if x.product_id==k) for k in cart}
            st.caption(' · '.join(f'{short_name(product_byid[k].name)} {delivered[k]}/{q}개' for k,q in cart.items()))
        with st.expander('상품별 가격·행사·재고 비교'):
            st.dataframe(brand_offer_rows(calculation_stores,calculation_offers,active_products,cart,settings),hide_index=True,width='stretch')
            st.caption('브랜드별 범위입니다. 가격·행사·재고는 점포마다 다를 수 있습니다.')
        with st.expander('증정 수량 제안'):
            suggestions={}
            for o in snapshot.offers:
                if o.product_id not in cart or store_byid[o.store_id].brand not in brands:continue
                promo,_=promotion_state(o,settings.as_of)
                if not promo:continue
                qty=cart[o.product_id];bundle=int(promo[0])+1;more=((qty+bundle-1)//bundle)*bundle
                if more==qty or more>12 or o.stock is None or o.stock<more or not o.price:continue
                a,b=cost(o,qty,settings.as_of),cost(o,more,settings.as_of)
                key=(o.product_id,more);candidate=(b-a,b,o.store_id,o,a)
                if key not in suggestions or candidate[:3]<suggestions[key][:3]:suggestions[key]=candidate
            for (pid,more),(extra,amount,sid,o,before) in sorted(suggestions.items()):
                st.markdown('**'+short_name(product_byid[pid].name)+f' · {cart[pid]} → {more}개**')
                st.caption(f'{store_byid[sid].name} 기준 {before:,} → {amount:,}원 ({extra:+,}원)')
                st.button(f'{more}개로 적용',key=f'extra_{sid}_{pid}',on_click=apply_quantity,args=(pid,more))
            if not suggestions:st.caption('추가로 적용할 증정 수량이 없습니다.')
        if received_snapshot and received_snapshot.observations:
            with st.expander('받은 자료와 확인 시각',expanded=False):
                st.dataframe(observation_rows(received_snapshot.observations,received_snapshot.stores),hide_index=True,width='stretch')
                if data_mode=='LIVE':
                    for notice in received_snapshot.messages:st.caption(notice)
        if data_mode=='LIVE':
            unlinked=[short_name(p.name)+': '+', '.join(b for b in brands if b not in {c.brand for c in active_links.get(p.id,())}) for p in active_products if any(b not in {c.brand for c in active_links.get(p.id,())} for b in brands)]
            if unlinked:st.caption('같은 상품 연결 미확인 — '+' / '.join(unlinked))
            st.caption('가격·정확한 재고·위치가 확인된 범위의 추천입니다. 방문 시 달라질 수 있습니다.')
            if not received_snapshot or not received_snapshot.observations:
                for notice in snapshot.messages:st.caption(notice)
