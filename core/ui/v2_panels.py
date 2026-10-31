from dataclasses import replace
from io import StringIO
import csv
import hashlib

import streamlit as st

from core.services.budget_optimizer import optimize_budget
from core.services.split_purchase import compare_purchase_plans
from core.services.quantity_suggestions import suggest_quantity
from core.services.product_list import product_key, product_token
from core.services.catalog import catalog_link_for
from core.services.distance import haversine_km
from core.ui.product_images import product_photo
from core.ui.v2_actions import comparison_context, apply_quantities, apply_substitute


def _live_observations(rows,statuses):
    """해당 브랜드의 성공 범위가 명시되지 않은 결과를 확정 자료로 승격하지 않는다."""
    states={}
    for status in statuses:
        states.setdefault(status.brand,set()).add(status.source)
    result=[]
    for row in rows:
        sources=states.get(row.brand,set())
        if sources=={'LIVE'}:
            result.append(row)
        else:
            source=next((value for value in ('ERROR','PARTIAL','CACHE') if value in sources),'UNKNOWN')
            result.append(replace(row,data_source=source))
    return result


def render_v2(stores, cart, budget, lat, lng, radius, brands, mode):
    st.subheader('구매 도우미',icon=':material/tips_and_updates:')
    st.session_state.setdefault('v2_tool','예산 맞추기')
    choice=st.pills('도움받을 기능',['예산 맞추기','두 매장 비교','품절 대체','행사 수량'],
                    default=None,key='v2_tool')
    st.caption('가격·행사·필요 재고가 확인된 자료로만 계산합니다. 변경안은 선택해야 적용됩니다.')
    context=comparison_context()
    if choice=='예산 맞추기':
        _budget(stores,cart,budget,context)
    elif choice=='두 매장 비교':
        _split(stores,cart,budget,lat,lng)
    elif choice=='행사 수량':
        _quantities(stores,cart,budget,context)
    elif choice=='품절 대체':
        _substitutes(stores,cart,lat,lng,radius,brands,mode,context)


def _budget(stores,cart,budget,context):
    st.write('유지할 상품은 그대로 두고, 나머지 수량을 조정합니다.')
    st.caption('우선순위: 줄이는 개수 최소 → 바꾸는 상품 수 최소 → 낮은 결제액. 빈 장바구니는 제안하지 않습니다.')
    locked={i.item_id for i in cart if st.session_state.get(f'locked_{i.item_id}',False)}
    if budget<=0:
        st.info('왼쪽에서 예산을 설정하면 수량 조정안을 확인할 수 있습니다.')
        return
    plans=optimize_budget(cart,stores,int(budget),locked)
    if not plans:
        st.info('유지할 상품과 확인된 재고 조건으로는 예산을 맞출 수 없습니다. 예산이나 유지 조건을 조정해 주세요.')
    by_key={s.store_key:s for s in stores}
    for index,plan in enumerate(plans):
        with st.container(border=True):
            st.markdown(f'**{plan.store_name} · {plan.total:,}원**')
            st.caption(f'예산 잔액 {budget-plan.total:,}원 · 줄이는 수량 {plan.removed_units}개 · 변경 상품 {plan.changed_items}종')
            for item in cart:
                new=plan.quantities[item.item_id]
                if new!=item.quantity:
                    st.write(f'{item.label}: {item.quantity}개 → '+(f'{new}개' if new else '제외'))
            if plan.removed_units==0:
                st.success('변경 없이 예산을 충족합니다.',icon=':material/check:')
            else:
                st.button('이 수량으로 변경',key=f'v2_budget_apply_{index}',
                          on_click=apply_quantities,args=(plan.quantities,by_key[plan.store_key],context,True))


def _split(stores,cart,budget,lat,lng):
    st.write('한 곳에서 살 때와 두 곳에 나누어 살 때를 비교합니다.')
    st.caption('한 상품의 수량은 한 매장에서 전부 구매합니다. 이동거리는 출발지→매장 직선거리이며 도보 경로·시간은 아닙니다.')
    plans=compare_purchase_plans(cart,stores,lat,lng,int(budget))
    if not plans:
        st.info('최대 두 매장으로도 전체 상품을 살 수 있는 확인된 조합이 없습니다.')
        return
    one=next((p for p in plans if p.store_count==1),None)
    labels={i.item_id:i for i in cart}
    by_key={s.store_key:s for s in stores}
    ordered=([one] if one else [])+[p for p in plans if p.store_count==2]
    with st.container(horizontal=True,gap='medium'):
        for index,plan in enumerate(ordered):
            with st.container(border=True,width=360):
                st.markdown(f'**{plan.store_count}개 매장 · {plan.total:,}원**')
                st.write(' → '.join(plan.store_names))
                st.caption(('예산 이내' if plan.within_budget else '예산 초과')+' · '+
                           (f'직선 이동거리 {plan.distance_km:.2f}km' if plan.distance_km is not None else '전체 이동거리 미확인'))
                if one and plan.store_count==2:
                    difference=one.total-plan.total
                    st.write(f'한 매장 구매 대비 {abs(difference):,}원 '+('절약' if difference>=0 else '추가')+' · 방문 1곳 추가')
                rows=[]
                for item_id,key in plan.assignments.items():
                    item=labels[item_id]
                    st.write(f'{by_key[key].store_name}: {item.label} {item.quantity}개')
                    rows.append([by_key[key].store_name,item.label,item.quantity])
                data=StringIO();writer=csv.writer(data);writer.writerow(['매장','상품','수량']);writer.writerows(rows)
                writer.writerow(['총액',plan.total,'']);writer.writerow(['직선 이동거리(km)',plan.distance_km if plan.distance_km is not None else '미확인',''])
                st.download_button('구매 목록 CSV 저장',data.getvalue().encode('utf-8-sig'),
                                   file_name=f'구매안_{plan.store_count}개매장.csv',key=f'v2_split_csv_{index}',mime='text/csv')


def _quantities(stores,cart,budget,context):
    if not stores:
        st.info('먼저 매장을 비교해 주세요.')
        return
    by_key={s.store_key:s for s in stores}
    key=st.selectbox('행사를 확인할 매장',list(by_key),format_func=lambda k:by_key[k].store_name,key='v2_quantity_store')
    store=by_key[key]
    st.write('행사 묶음을 채웠을 때 더 내는 금액을 확인하세요.')
    found=False
    for item,line in zip(cart,store.lines):
        hint=suggest_quantity(item,line)
        if hint is None:
            continue
        found=True
        with st.container(border=True):
            st.markdown(f'**{item.label} · {hint.promotion}**')
            st.write(f'{hint.original_quantity}개 {hint.original_total:,}원 → {hint.suggested_quantity}개 {hint.suggested_total:,}원')
            st.caption(f'추가 결제 {hint.extra_cost:,}원 · 받을 상품 {hint.suggested_quantity-hint.original_quantity}개 추가')
            if budget>0 and store.total is not None and store.total+hint.extra_cost>budget:
                st.caption('이 매장의 장바구니 총액은 현재 예산을 넘습니다.')
            quantities={i.item_id:i.quantity for i in cart};quantities[item.item_id]=hint.suggested_quantity
            st.button(f'{hint.suggested_quantity}개로 변경',key=f'v2_quantity_apply_{item.item_id}',
                      on_click=apply_quantities,args=(quantities,store,context))
    if not found:
        st.info('현재 수량이 행사 묶음에 맞거나, 추가 수량의 재고·가격을 확인하지 못했습니다.')


def _substitutes(stores,cart,lat,lng,radius,brands,mode,context):
    from core.services.substitutions import SubstituteProduct,find_substitutes,load_substitute_catalog
    items={i.item_id:i for i in cart}
    target=st.selectbox('대체할 상품',list(items),format_func=lambda k:items[k].label,key='v2_sub_target')
    item=items[target]
    originals={s.store_key:(s,next(line.record for line in s.lines if line.item_id==target))
               for s in stores if any(line.item_id==target and line.record is not None
                                      and line.pricing_state=='CONFIRMED' for line in s.lines)}
    if not originals:
        st.info('원상품의 가격·행사 자료가 필요합니다. 먼저 해당 상품을 다시 조회해 주세요.')
        return
    original_key=st.selectbox('원상품을 확인할 매장',list(originals),format_func=lambda k:originals[k][0].store_name,key='v2_sub_store')
    original=originals[original_key][1]
    st.caption(f'원상품 재고: {original.stock_display} · 선택한 대체품은 확인 후 직접 적용합니다.')
    max_extra=st.number_input('상품 전체의 추가 결제 허용액 (원)',0,100000,0,step=100,key='v2_sub_extra')
    tolerance=st.slider('용량 차이 허용 (%)',0,50,25,step=5,key='v2_sub_tolerance')/100
    keyword=st.text_input('대체 상품 검색어',value='대체' if mode=='TEST' else '',key='v2_sub_keyword')
    query_context=(context,target,original_key,keyword.strip(),lat,lng,radius,tuple(brands),mode)
    if st.button('대체 상품 조회',key='v2_sub_search',icon=':material/search:'):
        if not keyword.strip():
            st.warning('찾을 대체 상품명을 입력해 주세요.')
        else:
            with st.spinner('대체 상품 목록을 찾고 있습니다.' if mode=='LIVE' else '시연 대체 후보를 확인하고 있습니다.'):
                try:
                    if mode=='LIVE':
                        rows,statuses=st.session_state.manager.search_products(keyword.strip(),lat,lng,brands,mode)
                    else:
                        rows,statuses=st.session_state.manager.search_all(keyword.strip(),lat,lng,brands,mode,include_unpriced=True)
                    sources={s.brand:s.source for s in statuses}
                    rows=[replace(r,data_source=sources[r.brand]) if sources.get(r.brand) in ('CACHE','ERROR','PARTIAL') else r for r in rows]
                    # 상품 목록에는 점포 좌표가 없다. 실제 선택 재고를 확인한 뒤 반경을 적용한다.
                    st.session_state.v2_alt_rows=rows if mode=='LIVE' else [r for r in rows if (d:=haversine_km(lat,lng,r.latitude,r.longitude)) is not None and d<=radius]
                    st.session_state.v2_alt_context=query_context
                    st.session_state.v2_alt_statuses=[f'{s.brand}: {s.message}' for s in statuses]
                    st.session_state.v21_sub_stock_context=None
                    st.session_state.v21_sub_stock_rows=[]
                    for status in statuses:
                        if status.source in ('ERROR','PARTIAL','CACHE'):
                            st.warning(f'{status.brand}: {status.message}')
                except Exception:
                    st.session_state.v2_alt_rows=[];st.session_state.v2_alt_context=query_context
                    st.session_state.v21_sub_stock_context=None
                    st.session_state.v21_sub_stock_rows=[]
                    st.error('대체 후보를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.')
    if st.session_state.get('v2_alt_context')!=query_context:
        st.caption('후보 조회 후 같은 종류와 용량 조건에 맞는 상품을 제안합니다.')
        return
    rows=st.session_state.get('v2_alt_rows',[])
    if not rows:
        st.info('조회된 대체 후보가 없습니다. 검색어나 비교 브랜드를 바꿔 보세요.')
        return
    metadata=load_substitute_catalog()
    approval_context=None
    if mode=='LIVE':
        candidates={product_key(r):r for r in rows}
        selected=st.selectbox('직접 허용할 대체 상품',list(candidates),format_func=lambda k:f'{k[0]} · {k[3]}',key='v2_sub_candidate')
        candidate=candidates[selected]
        st.caption('실시간 상품의 분류·용량표는 자동으로 추측하지 않습니다. 제품 표시를 확인해 입력하세요.')
        unit=st.selectbox('용량 비교 단위',['ml','g','개'],key='v2_sub_unit')
        original_amount=st.number_input('원상품 용량',min_value=0.1,value=250.0,key='v2_sub_original_amount')
        candidate_amount=st.number_input('대체품 용량',min_value=0.1,value=250.0,key='v2_sub_candidate_amount')
        approval_context=(query_context,target,item.quantity,original_key,product_key(original),selected,unit,original_amount,candidate_amount,int(max_extra),tolerance)
        st.session_state.v2_approval_context=approval_context
        approval_key='v2_sub_approve_'+hashlib.sha256(repr(approval_context).encode()).hexdigest()[:16]
        approved=st.checkbox('두 상품이 같은 종류이고 입력한 용량이 맞음을 확인했습니다.',key=approval_key)
        st.session_state.v2_sub_approved=approved
        if not approved:
            return
        group=f'user-approved:{item.item_id}'
        metadata=[SubstituteProduct(r.brand,r.barcode,r.product_code,r.product_name,group,amount,unit)
                  for r,amount in ((original,original_amount),(candidate,candidate_amount))]
        stock_context=(approval_context,int(max_extra),tolerance)
        st.caption('선택한 대체품만 재고를 확인합니다. 목록의 참고가는 구매 금액에 사용하지 않습니다.')
        if st.button('선택 대체품 재고 확인',key='v21_sub_check_stock',icon=':material/inventory_2:'):
            st.session_state.v21_sub_stock_context=stock_context
            st.session_state.v21_sub_stock_rows=[]
            st.session_state.v21_sub_stock_statuses=[]
            try:
                with st.spinner('선택한 대체품의 매장 재고를 확인하고 있습니다.'):
                    stock_rows,statuses=st.session_state.manager.search_selected(
                        catalog_link_for(candidate,item.item_id),lat,lng,mode='LIVE',force_refresh=False)
                stock_rows=_live_observations(stock_rows,statuses)
                # 반환된 원상품/별칭 관측을 숨기지 않아 동시각의 상충 정보도 검사한다.
                st.session_state.v21_sub_stock_rows=[r for r in stock_rows
                    if (d:=haversine_km(lat,lng,r.latitude,r.longitude)) is not None and d<=radius]
                st.session_state.v21_sub_stock_statuses=[(status.source,f'{status.brand}: {status.message}') for status in statuses]
            except Exception:
                st.session_state.v21_sub_stock_statuses=[('ERROR','선택한 대체품의 재고를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.')]
        if st.session_state.get('v21_sub_stock_context')!=stock_context:
            st.info('종류와 용량을 확인한 뒤 선택 대체품 재고 확인을 눌러 주세요.')
            return
        for source,message in st.session_state.get('v21_sub_stock_statuses',[]):
            if source in ('ERROR','PARTIAL','CACHE'):
                st.warning(message)
            else:
                st.caption(message)
        rows=[original]+st.session_state.get('v21_sub_stock_rows',[])
        # Keep original/candidate aliases and duplicate observations so conflicts
        # cannot be hidden by a UI filter. Only these two metadata identities
        # authorize suggestions; unrelated products have no matching metadata.
    else:
        st.caption('시연에서는 미리 작성한 가상 상품 분류·용량표를 사용합니다.')
    options=find_substitutes(original,rows,item.quantity,catalog=metadata,max_extra=int(max_extra),volume_tolerance=tolerance)
    if not options:
        st.info('분류·용량·추가 금액·확인된 재고 조건을 모두 만족하는 대체품이 없습니다.')
    for index,option in enumerate(options[:6]):
        row=option.record
        with st.container(border=True):
            image,available=product_photo(row)
            st.image(image,width=160,alt=f'{row.product_name} 제품 사진' if available else f'{row.product_name}: 사진 없음')
            st.markdown(f'**{row.product_name} · {row.brand} {row.store_name}**')
            st.write(f'{item.quantity}개 {option.payable:,}원 · 원상품 대비 {option.extra_cost:+,}원')
            st.caption(option.reason)
            st.button('이 상품으로 교체',key=f'v2_sub_apply_{index}',on_click=apply_substitute,
                      args=(item.item_id,option,original,metadata,int(max_extra),tolerance,context,approval_context,rows))
