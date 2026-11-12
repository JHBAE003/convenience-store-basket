"""같은 구매 화면에 로컬 상품 그림과 실제 도보망을 더한 v3.1.1."""
import csv
import io
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

st.set_page_config(page_title='편의점 장바구니 v3.1.1',page_icon=':material/shopping_basket:',layout='wide')


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


st.session_state.setdefault('data_mode','TEST')
if st.session_state.get('dataset_revision')!='sqlite24-map-v311':switch_data_mode()
data_mode=st.session_state['data_mode']
if st.session_state.get('search_ui_revision')!='brand-cards-v1':
    # 이전 검색 캐시만 바꾸고 기존 장바구니와 확인 자료는 보존한다.
    st.session_state.update(search_result=None,search_ui_revision='brand-cards-v1',confirm_link=False)
    if data_mode=='TEST':st.session_state['_provider']=TestProvider()
provider=st.session_state['_provider']
st.title('편의점 장바구니')
st.caption(f'v3.1.1 · 구매 계획 비교 · {data_mode} 모드')
with st.sidebar:
    st.header('데이터 선택')
    st.radio('데이터 모드',('TEST','LIVE'),key='data_mode',on_change=switch_data_mode,
             format_func=lambda v:'TEST · 시연 데이터' if v=='TEST' else 'LIVE · 실제 조회')
    st.caption('두 모드는 같은 화면과 계산을 사용합니다. 모드를 바꾸면 장바구니·조회 결과를 초기화합니다.')
    st.header('구매 조건')
    st.selectbox('추천 기준',MODES,key='mode')
    st.multiselect('비교 브랜드',BRANDS,key='brands')
    st.slider('최대 방문 매장 수',1,3,key='max_stores')
    st.number_input('예산 (원)',min_value=0,max_value=1000000,step=100,key='budget')
    st.checkbox('예산 엄격하게 적용',key='strict_budget',help='0원도 실제 예산입니다. 예산 내 구매 모드는 항상 엄격 적용됩니다.')
    st.number_input('출발지에서 각 매장까지 반경 (km)',min_value=0.0,max_value=30.0,step=0.1,key='radius_km')
    st.radio('거리 계산',('도보 경로','직선 거리 (참고)'),key='distance_basis',
             help='도보 경로는 내려받은 서울대입구역 주변 도로망 안에서 계산합니다. 범위 밖은 직선 거리로 직접 바꿔 참고할 수 있습니다.')
    st.number_input('전체 이동거리 상한 (km)',min_value=0.0,max_value=100.0,step=0.1,key='route_limit_km')
    st.checkbox('출발지로 돌아오기',key='return_home')
    with st.expander('출발 위치 직접 설정'):
        st.number_input('출발 위도',min_value=-90.0,max_value=90.0,format='%.5f',key='origin_lat')
        st.number_input('출발 경도',min_value=-180.0,max_value=180.0,format='%.5f',key='origin_lng')
        st.caption('TEST 기본값은 서울대입구역, LIVE 기본값은 서울시청입니다. LIVE 조회 버튼을 누르면 이 좌표를 외부 조회에 사용합니다.')
    with st.expander('가격·거리 균형 조절'):
        st.number_input('거리 부담값 (원/km)',min_value=0,max_value=100000,step=100,key='distance_weight')
        st.number_input('추가 방문 부담값 (원/곳)',min_value=0,max_value=100000,step=100,key='visit_weight')
        st.caption('선호도 비교용 값이며 실제 교통비가 아닙니다.')

if data_mode=='TEST':
    st.info('서울대입구역 주변 실제 점포명·위치 24곳을 사용합니다. 상품·가격·행사·재고는 모두 가상입니다. 행사 기준일은 '+provider.metadata['fixed_date']+'로 고정됩니다.')
    st.caption('브랜드별 6곳 · 점포당 300종 · 가상 판매 자료 7,200건 · SQLite 파일을 직접 조회합니다.')
    with st.expander('브랜드별 TEST 데이터와 출처'):
        locations=provider.dataset.stores()
        rows=[]
        for brand in BRANDS:
            points=[r for r in locations if r['brand']==brand]
            rows.append({'브랜드':brand,'포함 점포 수':len(points),'공통 가상 상품 수':300,
                         '점포 위치 출처':', '.join(sorted({r['source_kind'] for r in points})),
                         '확인일':', '.join(sorted({r['checked_at'][:10] for r in points})),
                         '대표 출처 링크':points[0]['source_url'] if points else ''})
        st.dataframe(rows,hide_index=True,width='stretch')
        st.caption('공개 목록에 등재된 점포를 조사했습니다. 현재 영업 여부나 실제 판매 품목·재고를 확인했다는 뜻은 아닙니다. 가상 상품은 [가상]과 TEST- 코드로 구분합니다.')

else:
    st.info('실제 조회 모드입니다. 확인한 범위만 표시하며, 조회 실패를 시연 데이터로 대체하지 않습니다. 상품 검색과 가격·재고 확인은 버튼을 누를 때 실행합니다.')

origin=(st.session_state['origin_lat'],st.session_state['origin_lng'])
brands=st.session_state['brands']
st.subheader('상품 검색')
with st.form('product_search'):
    keyword=st.text_input('상품 이름',key='search_keyword',max_chars=80,placeholder='예: 우유')
    searched=st.form_submit_button('상품 검색',key='search_button')
if searched:
    reset_confirmation()
    with st.spinner('상품 후보를 확인하고 있습니다.'):
        st.session_state['search_result']=provider.search(keyword.strip(),origin,brands) if keyword.strip() else None
        st.session_state['search_context']=(origin,tuple(brands),keyword.strip())
    if not keyword.strip():st.warning('검색할 상품 이름을 입력해 주세요.')
search=st.session_state.get('search_result')
search_context=st.session_state.get('search_context')
if search and search_context and search_context[:2]==(origin,tuple(brands)):
    for message in search.messages:st.caption(message)
    if search.candidates:
        grouped={brand:group_search_candidates(c for c in search.candidates if c.brand==brand) for brand in brands}
        st.write('**브랜드별 검색 결과**')
        st.caption('선택한 브랜드에서 받은 상품을 모두 표시합니다. 점포별로 반복 표시하지 않으며, 참고 판매가는 현재 점포의 확정 판매가와 다를 수 있습니다.')
        for tab,(brand,groups) in zip(st.tabs([f'{brand} · {len(groups)}종' for brand,groups in grouped.items()]),grouped.items()):
            with tab:
                if not groups:st.caption('받은 검색 결과가 없습니다. 위 조회 안내를 확인해 주세요.')
                for offset in range(0,len(groups),3):
                    cards=st.columns(3)
                    for column,group in zip(cards,groups[offset:offset+3]):
                        c=group[0]
                        with column:
                            with st.container(border=True):
                                st.image(str(icon_path(c.seed_id)),alt=c.name+' 구분용 그림',width=160)
                                st.markdown('**'+c.name+'**');st.caption(c.spec)
                                st.caption('브랜드: '+brand)
                                prices=[x.reference_price for x in group if x.reference_price and x.reference_price>0]
                                st.caption('참고 판매가: '+(f'{min(prices):,}원' if prices else '미확인'))
                                existing=next((pid for pid,cs in st.session_state['links'].items() if any(x.id==c.id for x in cs)),None)
                                in_cart=existing is not None and st.session_state.get('qty_'+existing,0)>0
                                st.button('담긴 상품' if in_cart else '담기',key='search_add_'+c.id,
                                          on_click=add_candidate,args=(c.id,),disabled=in_cart,width='stretch')
        # 식별이 불확실한 실제 상품의 수동 연결은 필요할 때만 펼쳐서 사용한다.
        # 일반 담기 버튼에는 이 선택이나 이전 연결 설정을 적용하지 않는다.
        if data_mode=='LIVE' and st.session_state['products']:
            with st.expander('다른 브랜드의 같은 상품 연결 (선택)',expanded=False):
                by_id={c.id:c for c in search.candidates}
                targets={p.id:p.name+' · '+p.spec for p in st.session_state['products']}
                st.selectbox('연결할 검색 상품',list(by_id),key='manual_candidate_id',
                    format_func=lambda id:by_id[id].brand+' · '+by_id[id].name+' · '+by_id[id].spec,on_change=reset_confirmation)
                st.selectbox('연결할 장바구니 상품',list(targets),key='manual_link_target',format_func=targets.get,on_change=reset_confirmation)
                st.checkbox('기존 상품과 용량·맛·포장 수가 같은 것을 확인했습니다',key='confirm_link')
                st.button('같은 상품으로 연결',key='manual_link',on_click=link_selected_candidate,
                          disabled=not st.session_state.get('confirm_link'))
    else:st.warning('상품 후보를 확인하지 못했습니다. 검색어·브랜드 또는 조회 상태를 확인해 주세요.')
if st.session_state.get('add_message'):st.caption(st.session_state.pop('add_message'))

st.caption('상품 구분용 그림이며 실제 포장 사진이 아닙니다. 그림은 가격·재고 확인의 근거로 사용하지 않습니다.')
if data_mode=='TEST' and st.toggle('가상 상품 300종 그림 목록 보기',key='show_catalog'):
    catalog=provider.dataset.catalog()
    category=st.selectbox('상품 분류',('전체',)+tuple(dict.fromkeys(p['category'] for p in catalog)),
                          key='catalog_category',on_change=reset_gallery_page)
    visible=[p for p in catalog if category=='전체' or p['category']==category]
    pages=(len(visible)+11)//12
    page=st.selectbox('목록 페이지',range(1,pages+1),key='catalog_page',format_func=lambda p:f'{p} / {pages} 페이지')
    st.caption(f'{len(visible)}종 중 {(page-1)*12+1}~{min(page*12,len(visible))}번째 · 한 번에 12종씩 표시합니다.')
    gallery=st.columns(4)
    for i,p in enumerate(visible[(page-1)*12:page*12]):
        with gallery[i%4]:
            st.image(str(icon_path(p['id'])),alt=p['name']+' 구분용 그림',width=150)
            st.markdown('**'+p['name']+'**');st.caption(p['spec'])
            st.button('장바구니에 담기',key='catalog_add_'+p['id'],on_click=add_from_gallery,args=(p,))

products=st.session_state['products'];links=st.session_state['links']
product_byid={p.id:p for p in products}
st.subheader('내 장바구니')
if not products:st.caption('상품을 검색해 장바구니에 담아 주세요.')
cols=st.columns(min(3,max(1,len(products))))
for i,p in enumerate(products):
    with cols[i%len(cols)]:
        st.image(str(icon_path(p.id)),alt=p.name+' 구분용 그림',width=160)
        st.markdown(f'**{p.name}**');st.caption(p.spec)
        st.caption('연결 브랜드: '+', '.join(c.brand for c in links.get(p.id,())))
        st.number_input(f'{p.name} 받을 수량',min_value=0,max_value=12,step=1,key='qty_'+p.id)
st.caption('증정분을 포함해 실제 받을 개수입니다. 0개는 조회·계산에서 제외합니다. 최대 상품 6종입니다.')
st.button('장바구니 비우기',key='clear_cart',on_click=clear_cart)
cart={p.id:st.session_state.get('qty_'+p.id,0) for p in products if st.session_state.get('qty_'+p.id,0)>0}
active_products=tuple(p for p in products if p.id in cart)
active_links={pid:cs for pid,cs in links.items() if pid in cart}
context=context_for(active_products,active_links)
mode=st.session_state['mode']
settings=Settings(origin=origin,brands=tuple(brands),max_stores=st.session_state['max_stores'],
    radius_km=st.session_state['radius_km'],route_limit_km=st.session_state['route_limit_km'],
    return_home=st.session_state['return_home'],budget=st.session_state['budget'],
    strict_budget=st.session_state['strict_budget'] or mode==MODES[4],
    distance_weight=st.session_state['distance_weight'],visit_weight=st.session_state['visit_weight'],
    as_of=provider.metadata['fixed_date'] if data_mode=='TEST' else korean_today())
st.subheader('가격·재고 확인')
refresh=st.button('가격·재고 확인',key='lookup_button',disabled=not cart or not brands,type='primary')
force=st.checkbox('저장 결과를 건너뛰고 다시 요청',key='force_refresh')
st.caption('재요청은 앱의 저장 결과를 건너뜁니다. 외부 서비스의 자체 캐시까지 갱신한다는 뜻은 아닙니다.')
snapshot=st.session_state.get('snapshot')
if refresh:
    with st.spinner('선택한 상품의 가격·재고를 확인하고 있습니다.'):
        snapshot=provider.lookup(active_products,active_links,origin,brands,context,force=force)
        st.session_state['snapshot']=snapshot
if snapshot:
    for message in snapshot.messages:st.caption(message)
    if snapshot.observations:
        with st.expander('받은 자료와 확인 시각',expanded=False):
            st.dataframe(observation_rows(snapshot.observations,snapshot.stores),hide_index=True,width='stretch')
            st.caption('수신한 자료를 브랜드·상품별로 요약했습니다. 같은 브랜드 안에서도 여러 시각의 자료가 섞일 수 있으며 최신 시각 하나로 바꾸지 않습니다.')
    if not snapshot.usable(context):
        st.warning('조회 조건이 바뀌었거나 보관 시간이 지났습니다. 위 자료는 이전 조회 기록이며 현재 추천에 사용하지 않습니다. 가격·재고 확인을 다시 눌러 주세요.')
        snapshot=Snapshot(context=context)
else:
    st.caption('가격·재고 확인을 누르면 연결한 브랜드의 상품만 조회합니다.')
    snapshot=Snapshot(context=context)
store_byid={s.id:s for s in snapshot.stores}
unlinked=[f'{p.name}: '+', '.join(b for b in brands if b not in {c.brand for c in active_links.get(p.id,())}) for p in active_products
          if any(b not in {c.brand for c in active_links.get(p.id,())} for b in brands)]
if unlinked:st.caption('같은 상품 연결이 없어 조회하지 않은 브랜드 — '+' / '.join(unlinked))
if data_mode=='LIVE':st.caption('점포 판매가·정확한 재고 수량·좌표가 확인된 항목만 구매안에 포함합니다. 추천은 조회가 성공한 범위에 한정되며 방문 시 달라질 수 있습니다.')
calculation_stores,calculation_offers,scope_issue=calculation_scope(snapshot,settings)
if scope_issue:st.warning(scope_issue)
walking=st.session_state['distance_basis']=='도보 경로'
distance_label='도보 이동 (연결 추정 포함)' if walking else '직선 이동'
network=None;distance_matrix=None
if walking:
    distance_matrix=()  # 지도 오류도 직선 거리로 자동 전환하지 않는다.
    if calculation_stores and cart:
        try:
            points=((ORIGIN,settings.origin),)+tuple((s.id,(s.lat,s.lng)) for s in calculation_stores)
            network=get_network(tuple(sorted(points)))
            distance_matrix=network.matrix()
            if network.issues:
                st.warning('지도에서 연결하지 못한 위치: '+' / '.join(('출발지' if k==ORIGIN else store_byid[k].name)+': '+v for k,v in network.issues.items()))
            unreachable=[s.name for s in calculation_stores if network.distance(ORIGIN,s.id)==float('inf') and s.id not in network.issues]
            if unreachable:st.warning('연결된 도보 경로를 찾지 못한 매장: '+', '.join(unreachable))
        except (OSError,ValueError,KeyError) as exc:
            st.warning('다운로드한 도보 지도를 사용할 수 없습니다. 지도 파일을 확인하거나 거리 계산을 직선 참고로 변경해 주세요.')
    st.caption('서울대입구역 주변에 내려받은 도로·보행로로 계산합니다. 점포 위치와 보행로 사이 연결은 추정 거리이며, 건물 출입구·현재 통행 가능 여부는 확인되지 않았습니다.')
else:st.caption('거리와 방문 순서는 좌표 사이 직선을 기준으로 계산합니다. 실제 도보 거리와 다릅니다.')
result=compare_plans(active_products,calculation_stores,calculation_offers,cart,settings,distance_matrix)
st.session_state['last_distance_basis']=st.session_state['distance_basis']
st.session_state['last_summary']={m:({'total':p.total,'route':p.route,'distance':p.distance_km} if p else None) for m,p in result.winners.items()}
st.session_state['last_plan_count']=len(result.plans)

def describe(plan):return ' → '.join(store_byid[k].name for k in plan.route)
def summary_row(plan,labels):
    return {'결제액 (원)':plan.total,distance_label+' (m)':round(plan.distance_km*1000),
            '방문 수':plan.count,'예산 충족':'충족' if plan.total<=settings.budget else '초과',
            '추천 기준':labels,'매장':describe(plan),
            '예산 잔액/초과 (원)':settings.budget-plan.total,'균형 비교 점수':round(plan.score)}

chosen=result.winners[mode]
st.subheader('선택 기준의 대표 추천')
if chosen:
    with st.container(border=True):
        st.markdown(f'### {mode} · {describe(chosen)}')
        metrics=st.columns(2)
        metrics[0].metric('상품 결제액',f'{chosen.total:,}원')
        metrics[1].metric(distance_label,f'{chosen.distance_km*1000:,.0f}m')
        second=st.columns(2)
        second[0].metric('방문 매장',f'{chosen.count}곳')
        balance=settings.budget-chosen.total
        second[1].metric('예산 잔액' if balance>=0 else '예산 초과',f'{abs(balance):,}원')
        if mode==MODES[3]:st.caption(f'균형 비교 점수 {chosen.score:,.0f}점 — 결제액·교통비와 구분하는 비교용 값입니다.')
        st.write(f'요청한 {sum(cart.values())}개를 모두 받는 구매안입니다. 선택 조건 안에서 {mode} 기준과 동률 처리 순서를 적용했습니다.')
else:
    st.warning(f'「{mode}」 조건을 만족하는 전체 구매안이 없습니다.')
    if data_mode=='LIVE':st.caption('확인되지 않은 가격·재고는 품절이나 0개를 뜻하지 않습니다. 계산에 쓸 수 있는 자료와 선택 조건을 확인해 주세요.')
    for reason in result.reasons:
        if data_mode=='LIVE' and '매장이 없습니다' in reason:
            reason='현재 조회 자료에는 선택 조건으로 계산할 수 있는 매장이 없습니다.'
        st.write('• '+reason)
    for pid,missing in result.shortages.items():
        if missing:st.write(f'{product_byid[pid].name}: '+(f'가격·재고를 모두 확인해 계산에 쓸 수 있는 수량이 요청보다 {missing}개 적습니다.' if data_mode=='LIVE' else f'확인된 재고 기준 {missing}개 부족'))

st.subheader('대표 구매안 비교')
representatives={}
for label,plan in result.winners.items():
    if plan:
        representatives.setdefault(plan.signature,[plan,[]])[1].append(label)
# Include actual 2/3-store options, even when a one-store winner exists.
for n in (2,3):
    best=min((p for p in result.plans if p.count==n),key=lambda p:rank(p,MODES[0]),default=None)
    if best:representatives.setdefault(best.signature,[best,[]])[1].append(f'{n}곳 최저 결제액')
entries=list(representatives.values())
if entries:
    st.dataframe([summary_row(p,' · '.join(labels)) for p,labels in entries],hide_index=True,width='stretch')
    st.caption('같은 구매안이 여러 기준에서 1위면 한 행에 표시합니다. 최저가는 선택한 조회 점포·상품·수량·제약 안에서의 값입니다.')
    options=[p.signature for p,_ in entries]
    default=options.index(chosen.signature) if chosen else 0
    selected=st.selectbox('구매 목록을 볼 계획',options,index=default,
        format_func=lambda sig:next(' · '.join(labels)+' / '+f'{p.total:,}원' for p,labels in entries if p.signature==sig))
    active=next(p for p,_ in entries if p.signature==selected)
    other=next((p for p,_ in entries if p.signature!=active.signature),None)
    if other:st.write(comparison_text(active,other,distance_label))
else:
    active=None
    st.caption('조건을 완화하거나 장바구니 수량을 직접 변경해 다시 비교할 수 있습니다.')

def purchase_rows(plan):
    return [{'매장':store_byid[line.store_id].name,'상품':product_byid[line.product_id].name,
             '규격':product_byid[line.product_id].spec,'받을 수량':line.quantity,'단가 (원)':line.unit_price,
             '적용 행사':line.promotion or '일반 판매','결제액 (원)':line.amount,'근거':line.note} for line in plan.lines]

if active:
    st.subheader('매장별 구매 목록')
    for i,store_id in enumerate(active.route,1):
        rows=[r for r,line in zip(purchase_rows(active),active.lines) if line.store_id==store_id]
        with st.container(border=True):
            st.markdown(f'**{i}. {store_byid[store_id].name} · {sum(r["결제액 (원)"] for r in rows):,}원**')
            st.dataframe(rows,hide_index=True,width='stretch')
    stream=io.StringIO();rows=purchase_rows(active)
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader()
    writer.writerows({k:csv_cell(v) for k,v in row.items()} for row in rows)
    st.download_button('구매 목록 CSV 저장',stream.getvalue().encode('utf-8-sig'),f'구매계획_{data_mode}.csv','text/csv')
    st.subheader('방문 순서와 '+('도보 경로 지도' if walking else '직선 지도'))
    st.write('출발지 → '+describe(active)+(' → 출발지' if settings.return_home else ''))
    if walking and network:
        svg=walking_svg(network,active.route,settings.return_home)
        st.image(svg,alt='실제 거리와 보행로 위에 출발지와 방문 순서를 표시한 도보 경로 지도',width='stretch')
        st.caption('초록 실선은 도로를 따르는 경로입니다. 황토색 점선은 위치에서 보행로까지의 미확인 연결입니다. 계단·횡단보도·통행 제한은 지도에 기록된 범위만 반영하며 현장 길안내를 보장하지 않습니다.')
        downloaded=datetime.fromisoformat(network.base.metadata['downloaded_at']).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M (한국 시간)')
        st.caption('지도 내려받은 시각: '+downloaded+' · 원본 전체 자료 갱신 시각: 미제공')
        st.markdown('지도 자료: [© OpenStreetMap contributors · ODbL 1.0](https://www.openstreetmap.org/copyright)')
        for i,(sid,leg) in enumerate(zip(active.route+((ORIGIN,) if settings.return_home else ()),network.legs(active.route,settings.return_home)),1):
            target='출발지' if sid==ORIGIN else store_byid[sid].name
            st.caption(f'{i}. {target}까지 {leg.distance_km*1000:,.0f}m (위치 연결 추정 포함)')
        exports=st.columns(2)
        exports[0].download_button('경로 지도 SVG 저장',svg.encode('utf-8'),'도보경로.svg','image/svg+xml')
        exports[1].download_button('도로 경로 GPX 저장',route_gpx(network,active.route,settings.return_home).encode('utf-8'),'도보경로.gpx','application/gpx+xml')
    else:
        st.image(route_svg(active,snapshot.stores,settings.origin,settings.return_home),alt='출발지와 번호가 있는 매장을 직선으로 연결한 참고 지도',width='stretch')
        st.caption('선은 좌표 사이의 직선 연결이며 실제 도보 경로·소요시간을 뜻하지 않습니다.')
elif result.partial:
    st.subheader('일부 수량만 가능한 참고안')
    st.warning('전체 구매 추천이 아닙니다. 장바구니는 변경되지 않습니다. 예산 내 일부 구매는 추가 1개 비용을 낮추는 참고 예시이며 최대 확보 수량을 보장하지 않습니다.')
    st.dataframe(purchase_rows(result.partial),hide_index=True)
    delivered={k:sum(x.quantity for x in result.partial.lines if x.product_id==k) for k in cart}
    st.write(' · '.join(f'{product_byid[k].name} {delivered[k]}/{q}개 확보, {q-delivered[k]}개 부족' for k,q in cart.items()))

with st.expander('상품별 가격·행사·재고 비교'):
    st.dataframe(brand_offer_rows(calculation_stores,calculation_offers,active_products,cart,settings),hide_index=True,width='stretch')
    st.caption('현재 반경 안에서 받은 자료를 브랜드별로 요약합니다. 가격·행사·재고는 점포마다 다를 수 있습니다. 재고 수량을 합쳐 한 점포의 재고처럼 표시하지 않으며, 구매 가능한 개별 점포는 추천·지도·구매 목록에서 확인합니다.')

with st.expander('추가 증정 수량 제안 · 적용 전 장바구니 유지'):
    suggestions=0
    for o in snapshot.offers:
        if o.product_id not in cart or store_byid[o.store_id].brand not in settings.brands:continue
        promo,_=promotion_state(o,settings.as_of)
        if not promo:continue
        qty=cart[o.product_id];bundle=int(promo[0])+1;more=((qty+bundle-1)//bundle)*bundle
        if more==qty or more>12 or o.stock is None or o.stock<more or not o.price:continue
        a,b=cost(o,qty,settings.as_of),cost(o,more,settings.as_of)
        st.write(f'{store_byid[o.store_id].name} · {product_byid[o.product_id].name}: {qty}→{more}개, '
                 f'해당 매장 지출 {a:,}→{b:,}원 ({b-a:+,}원), 개당 {a/qty:,.0f}→{b/more:,.0f}원. 적용 후 전체 계획을 다시 계산합니다.')
        st.button(f'{product_byid[o.product_id].name} {more}개로 적용',key=f'extra_{o.store_id}_{o.product_id}',
                  on_click=apply_quantity,args=(o.product_id,more));suggestions+=1
    if not suggestions:st.caption('현재 조건에서 별도로 제안할 추가 증정 수량이 없습니다.')

with st.expander('계산 근거·지원 범위·기존 실시간 기능'):
    st.write(f'확인한 매장 조합 {result.combinations_checked}개. 엔진 지원 범위: 상품 6종·상품별 12개·매장 24곳·방문 3곳. 확인되지 않은 항목은 계산에서 제외합니다. 품목이나 매장을 임의로 자르지 않습니다.')
    st.write('매장 조합을 모두 검사하고 품목별 수량 배분을 동적 계획법으로 결합합니다. 각 매장 조합의 방문 순서는 가능한 순열을 모두 비교합니다.')
    st.write('균형 점수 = 상품 결제액 + 선택한 기준의 이동거리 × 거리 부담값 + (방문 수−1) × 방문 부담값')
    st.write('동률: 최저 결제액은 거리→방문 수, 최단 이동은 결제액→방문 수, 균형은 방문 수→결제액, 예산은 거리→결제액→방문 수. 마지막은 매장 ID·수량 배정 순서로 고정합니다.')
    st.caption('TEST와 LIVE는 같은 화면·추천 엔진을 사용합니다. LIVE 조회는 버튼을 누를 때만 실행하며, 실패해도 TEST로 전환하지 않습니다. LIVE 데이터 수집 코드는 legacy_v211/의 보존 코드를 재사용합니다.')
