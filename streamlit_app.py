"""v3 TEST entrypoint. No live adapters, geocoder, or remote images imported."""
import csv
import io
from dataclasses import replace
import streamlit as st

from planner.engine import MODES, Settings, cost, promotion_state, solve, rank
from planner.scenarios import PRODUCTS, STORES, SCENARIOS, get_scenario
from planner.presentation import comparison_text, route_svg
from legacy_v211.core.security.csv_cells import csv_cell

st.set_page_config(page_title='편의점 장바구니 v3.0.0',page_icon=':material/shopping_basket:',layout='wide')

def load_scenario(id):
    scenario=get_scenario(id)
    st.session_state['scenario_id']=id
    for p in PRODUCTS:st.session_state['qty_'+p.id]=dict(scenario.cart).get(p.id,0)
    for key in ('budget','strict_budget','max_stores','radius_km','route_limit_km','return_home','distance_weight','visit_weight'):
        st.session_state[key]=getattr(scenario.settings,key)
    st.session_state['origin_lat']=scenario.settings.origin[0]
    st.session_state['origin_lng']=scenario.settings.origin[1]
    st.session_state['brands']=list(scenario.settings.brands)
    st.session_state['mode']=MODES[0]

def apply_quantity(pid,quantity):st.session_state['qty_'+pid]=quantity

if 'scenario_id' not in st.session_state:load_scenario(1)
scenario=get_scenario(st.session_state['scenario_id'])
product_byid={p.id:p for p in PRODUCTS};store_byid={s.id:s for s in STORES}

st.title('편의점 장바구니')
st.caption('v3.0.0 · 구매 계획 비교 · TEST 모드')
st.info('가상 시연 데이터입니다. 실제 편의점의 가격·행사·재고가 아닙니다. 행사 기준일은 2026-11-15로 고정됩니다.')
with st.expander('8가지 상황으로 시연하기',expanded=True):
    columns=st.columns(2)
    for i,s in enumerate(SCENARIOS):
        columns[i%2].button(f'{s.id}. {s.title}',key=f'scenario_{s.id}',on_click=load_scenario,args=(s.id,),width='stretch',type='primary' if s.id==scenario.id else 'secondary')
    st.caption(f'현재 상황: {scenario.title} — {scenario.description}')

with st.sidebar:
    st.header('구매 조건')
    st.selectbox('추천 기준',MODES,key='mode')
    st.multiselect('비교 브랜드',sorted({s.brand for s in STORES}),key='brands')
    st.slider('최대 방문 매장 수',1,3,key='max_stores')
    st.number_input('예산 (원)',min_value=0,max_value=1000000,step=100,key='budget')
    st.checkbox('예산 엄격하게 적용',key='strict_budget',help='0원도 실제 예산입니다. 예산 내 구매 모드는 항상 엄격 적용됩니다.')
    st.number_input('출발지에서 각 매장까지 반경 (km)',min_value=0.0,max_value=30.0,step=0.1,key='radius_km')
    st.number_input('전체 직선 이동거리 상한 (km)',min_value=0.0,max_value=100.0,step=0.1,key='route_limit_km')
    st.checkbox('출발지로 돌아오기',key='return_home')
    with st.expander('출발 위치 직접 설정'):
        st.number_input('출발 위도',min_value=-90.0,max_value=90.0,format='%.5f',key='origin_lat')
        st.number_input('출발 경도',min_value=-180.0,max_value=180.0,format='%.5f',key='origin_lng')
        st.caption('기본값: 서울시청 좌표. 주소 검색과 위치 전송은 하지 않습니다.')
    with st.expander('가격·거리 균형 조절'):
        st.number_input('거리 부담값 (원/km)',min_value=0,max_value=100000,step=100,key='distance_weight')
        st.number_input('추가 방문 부담값 (원/곳)',min_value=0,max_value=100000,step=100,key='visit_weight')
        st.caption('기본 1,000원/km·추가 1곳당 500원. 시간·수고를 가격과 비교하기 위한 사용자 선호값이며 실제 교통비가 아닙니다.')

st.subheader('내 장바구니')
cols=st.columns(len(PRODUCTS))
for col,p in zip(cols,PRODUCTS):
    with col:
        st.markdown(f'**{p.name}**')
        st.caption(p.spec)
        st.number_input(f'{p.name} 받을 수량',min_value=0,max_value=12,step=1,key='qty_'+p.id)
st.caption('수량은 증정분을 포함해 실제 받을 개수입니다. 0개는 제외합니다. 기본 구매안은 요청 수량을 정확히 충족합니다.')
cart={p.id:st.session_state['qty_'+p.id] for p in PRODUCTS if st.session_state['qty_'+p.id]>0}
mode=st.session_state['mode']
settings=Settings(origin=(st.session_state['origin_lat'],st.session_state['origin_lng']),
    brands=tuple(st.session_state['brands']),max_stores=st.session_state['max_stores'],
    radius_km=st.session_state['radius_km'],route_limit_km=st.session_state['route_limit_km'],
    return_home=st.session_state['return_home'],budget=st.session_state['budget'],
    strict_budget=st.session_state['strict_budget'] or mode==MODES[4],
    distance_weight=st.session_state['distance_weight'],visit_weight=st.session_state['visit_weight'])
result=solve(PRODUCTS,STORES,scenario.offers,cart,settings)
st.session_state['last_summary']={m:({'total':p.total,'route':p.route,'distance':p.distance_km} if p else None) for m,p in result.winners.items()}
st.session_state['last_plan_count']=len(result.plans)

def describe(plan):return ' → '.join(store_byid[k].name for k in plan.route)
def summary_row(plan,labels):
    return {'결제액 (원)':plan.total,'직선 이동 (m)':round(plan.distance_km*1000),
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
        metrics[1].metric('총 직선 이동거리',f'{chosen.distance_km*1000:,.0f}m')
        second=st.columns(2)
        second[0].metric('방문 매장',f'{chosen.count}곳')
        balance=settings.budget-chosen.total
        second[1].metric('예산 잔액' if balance>=0 else '예산 초과',f'{abs(balance):,}원')
        if mode==MODES[3]:st.caption(f'균형 비교 점수 {chosen.score:,.0f}점 — 결제액·교통비와 구분하는 비교용 값입니다.')
        st.write(f'요청한 {sum(cart.values())}개를 모두 받는 구매안입니다. 선택 조건 안에서 {mode} 기준과 동률 처리 순서를 적용했습니다.')
else:
    st.warning(f'「{mode}」 조건을 만족하는 전체 구매안이 없습니다.')
    for reason in result.reasons:st.write('• '+reason)
    for pid,missing in result.shortages.items():
        if missing:st.write(f'{product_byid[pid].name}: 확인된 재고 기준 {missing}개 부족')

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
    st.caption('같은 구매안이 여러 기준에서 1위면 한 행에 표시합니다. 최저가는 선택한 가상 점포·상품·수량·제약 안에서의 값입니다.')
    options=[p.signature for p,_ in entries]
    default=options.index(chosen.signature) if chosen else 0
    selected=st.selectbox('구매 목록을 볼 계획',options,index=default,
        format_func=lambda sig:next(' · '.join(labels)+' / '+f'{p.total:,}원' for p,labels in entries if p.signature==sig))
    active=next(p for p,_ in entries if p.signature==selected)
    other=next((p for p,_ in entries if p.signature!=active.signature),None)
    if other:st.write(comparison_text(active,other))
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
    st.download_button('구매 목록 CSV 저장',stream.getvalue().encode('utf-8-sig'),'구매계획_TEST.csv','text/csv')
    st.subheader('방문 순서와 직선 지도')
    st.write('출발지 → '+describe(active)+(' → 출발지' if settings.return_home else ''))
    st.image(route_svg(active,STORES,settings.origin,settings.return_home),alt='출발지와 번호가 있는 매장을 직선으로 연결한 방문 순서 지도',width='stretch')
    st.caption('로컬 SVG 지도입니다. 선은 좌표 사이의 직선 연결이며 실제 도보 경로·소요시간을 뜻하지 않습니다.')
elif result.partial:
    st.subheader('일부 수량만 가능한 참고안')
    st.warning('전체 구매 추천이 아닙니다. 장바구니는 변경되지 않습니다. 예산 내 일부 구매는 추가 1개 비용을 낮추는 참고 예시이며 최대 확보 수량을 보장하지 않습니다.')
    st.dataframe(purchase_rows(result.partial),hide_index=True)
    delivered={k:sum(x.quantity for x in result.partial.lines if x.product_id==k) for k in cart}
    st.write(' · '.join(f'{product_byid[k].name} {delivered[k]}/{q}개 확보, {q-delivered[k]}개 부족' for k,q in cart.items()))

with st.expander('상품별 가격·행사·재고 비교'):
    rows=[]
    for o in scenario.offers:
        if o.product_id not in cart or store_byid[o.store_id].brand not in settings.brands:continue
        qty=cart[o.product_id];promo,note=promotion_state(o,settings.as_of)
        known=type(o.stock) is int and o.stock>=0
        rows.append({'매장':store_byid[o.store_id].name,'상품':product_byid[o.product_id].name,
                     '규격':product_byid[o.product_id].spec,'단가 (원)':o.price,
                     '행사':o.promotion or '없음','행사 상태':note,
                     '재고':str(o.stock) if known else '미확인','요청 수량':qty,
                     '요청 수량 결제액':f'{cost(o,qty,settings.as_of):,}원' if known and o.stock>=qty and o.price else '재고 부족/미확인'})
    st.dataframe(rows,hide_index=True)

with st.expander('추가 증정 수량 제안 · 적용 전 장바구니 유지'):
    suggestions=0
    for o in scenario.offers:
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
    st.write(scenario.expected)
    st.write(f'확인한 매장 조합 {result.combinations_checked}개. 엔진 지원 범위: 상품 6종·상품별 12개·매장 12곳·방문 3곳. 현재 시연은 3종·4곳입니다. 품목이나 매장을 임의로 자르지 않습니다.')
    st.write('매장 조합을 모두 검사하고 품목별 수량 배분을 동적 계획법으로 결합합니다. 각 매장 조합의 방문 순서는 가능한 순열을 모두 비교합니다.')
    st.write('균형 점수 = 상품 결제액 + 직선 이동거리 × 거리 부담값 + (방문 수−1) × 방문 부담값')
    st.write('동률: 최저 결제액은 거리→방문 수, 최단 이동은 결제액→방문 수, 균형은 방문 수→결제액, 예산은 거리→결제액→방문 수. 마지막은 매장 ID·수량 배정 순서로 고정합니다.')
    st.caption('v2.1.1의 기존 LIVE 기능은 legacy_v211/에 보존했습니다. 별도 「기존 LIVE 실행.bat」로 실행합니다. 이 TEST 화면은 LIVE 어댑터·주소 검색·원격 사진을 초기화하지 않습니다.')
