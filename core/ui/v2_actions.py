"""Apply reviewed suggestions in callbacks, before quantity widgets render."""
from dataclasses import replace

import streamlit as st

from core.cart_models import CatalogLink
from core.services.catalog import matches_link
from core.services.v2_common import confirmed_cost


def comparison_context():
    state=st.session_state
    return (tuple((i.item_id,i.label,i.quantity) for i in state.cart),state.get('mode'),
            state.get('latitude'),state.get('longitude'),tuple(state.get('brands',[])),
            state.get('radius'),state.get('budget'),
            tuple((l.item_id,l.brand,l.barcode,l.product_code,l.product_name) for l in state.links),
            state.snapshot.completed_at if state.get('snapshot') else None)


def clear_comparison():
    st.session_state.snapshot=None
    st.session_state.recommendations=None
    st.session_state.v2_alt_rows=[]
    st.session_state.v2_alt_context=None


def apply_quantities(quantities, store, context, enforce_budget=False):
    if comparison_context()!=context:
        st.error('장바구니나 조건이 바뀌었습니다. 다시 비교한 뒤 선택해 주세요.')
        return
    cart=st.session_state.cart
    if set(quantities)!={i.item_id for i in cart}:
        st.error('구매안의 상품 구성이 현재 장바구니와 다릅니다.')
        return
    by_id={line.item_id:line for line in store.lines}
    total=0
    for item in cart:
        quantity=quantities[item.item_id]
        if type(quantity) is not int or not 0<=quantity<=10:
            st.error('수량 범위를 확인해 주세요.')
            return
        if enforce_budget and st.session_state.get(f'locked_{item.item_id}',False) and quantity!=item.quantity:
            st.error('유지할 상품의 수량은 줄일 수 없습니다.')
            return
        if not enforce_budget and quantity==item.quantity:
            continue
        line=by_id.get(item.item_id)
        amount=confirmed_cost(line,quantity) if line else (0 if quantity==0 else None)
        if amount is None:
            clear_comparison()
            st.error('확인 가능한 재고·가격의 유효 시간이 지났습니다. 매장을 다시 비교해 주세요.')
            return
        total+=amount
    if not any(quantities.values()):
        st.error('최소 한 상품은 남겨 주세요.')
        return
    if enforce_budget and st.session_state.get('budget',0)>0 and total>st.session_state.budget:
        st.error('현재 예산을 넘는 구매안입니다.')
        return
    st.session_state.cart=[replace(i,quantity=quantities[i.item_id]) for i in cart if quantities[i.item_id]>0]
    for item in cart:
        if quantities[item.item_id]>0:
            st.session_state[f'qty_{item.item_id}']=quantities[item.item_id]
        else:
            st.session_state.pop(f'qty_{item.item_id}',None)
            st.session_state.pop(f'locked_{item.item_id}',None)
    clear_comparison()
    st.toast('수량을 변경했습니다. 매장 비교하기로 새 결과를 확인하세요.',icon=':material/check:')


def apply_substitute(item_id, option, original, metadata, max_extra, tolerance, context, approval_context=None,evidence_rows=None):
    from core.services.substitutions import find_substitutes
    if comparison_context()!=context:
        st.error('장바구니나 조건이 바뀌었습니다. 다시 비교해 주세요.')
        return
    item=next((i for i in st.session_state.cart if i.item_id==item_id),None)
    if item is None:
        return
    if st.session_state.mode=='LIVE' and (not st.session_state.get('v2_sub_approved',False)
            or approval_context is None or st.session_state.get('v2_approval_context')!=approval_context):
        st.error('이 대체품의 종류와 용량을 먼저 확인해 주세요.')
        return
    row=option.record
    allowed=find_substitutes(original,evidence_rows if evidence_rows is not None else [row],item.quantity,catalog=metadata,
                             max_extra=max_extra,volume_tolerance=tolerance)
    from core.services.product_matching import same_product
    from core.services.store_matching import same_store
    if not any(same_product(a.record,row) and same_store(a.record,row)
               and a.payable==option.payable and a.extra_cost==option.extra_cost for a in allowed):
        st.error('대체품의 조건이나 데이터 유효 시간을 다시 확인해 주세요.')
        return
    active_ids={i.item_id for i in st.session_state.cart if i.item_id!=item_id}
    if any(link.item_id in active_ids and matches_link(row,link) for link in st.session_state.links):
        st.error('이미 장바구니에 담긴 상품입니다. 해당 상품의 수량을 직접 조정해 주세요.')
        return
    st.session_state.cart=[replace(i,label=row.product_name) if i.item_id==item_id else i for i in st.session_state.cart]
    st.session_state.links=[link for link in st.session_state.links if link.item_id!=item_id]
    st.session_state.links.append(CatalogLink(item_id,row.brand,row.barcode,row.product_code,row.product_name))
    clear_comparison()
    st.toast('대체품으로 변경했습니다. 선택 브랜드의 매장 재고를 다시 비교해 주세요.',icon=':material/check:')
