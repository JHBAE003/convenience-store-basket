import streamlit as st

from core.services.product_list import product_token
from core.ui.product_images import product_photo


def render_product_cards(products, add, quantity, demo_ids=None):
    """Native controls keep image cards keyboard-operable and wrap on mobile."""
    if not products:
        st.info('검색 결과가 없습니다. 상품명을 짧게 입력하거나 브랜드 필터를 바꿔 보세요.', icon=':material/search_off:')
        return
    page_count = (len(products) + 5) // 6
    st.caption(f'상품 {len(products)}개 · 사진과 용량을 확인한 뒤 담아 주세요.')
    page = st.selectbox('상품 목록 페이지', list(range(1, page_count + 1)),
                        format_func=lambda p: f'{p} / {page_count} 페이지', key='catalog_page') if page_count > 1 else 1
    with st.container(horizontal=True, gap='medium'):
        for row in products[(page-1)*6:page*6]:
            with st.container(border=True, width=260):
                image, available = product_photo(row)
                st.image(image, alt=f'{row.product_name} 제품 사진' if available else f'{row.product_name}: 사진 없음', width='stretch')
                st.caption(row.brand + (' · 시연 상품' if row.data_source == 'TEST' else ''))
                st.text(row.product_name)
                st.markdown(f'**{row.price:,}원**' if row.price else '**가격 미확인**')
                if row.data_source == 'CATALOG':
                    st.caption(f'참고 판매가 · {row.updated_at[:10]} 수집')
                elif row.data_source == 'TEST':
                    st.caption('가상 가격 · 실제 판매 상품이 아닙니다.')
                else:
                    st.caption('조회 점포 판매가 · 재고는 매장 비교에서 확인')
                if not available:
                    st.caption('사진 준비 중' if not row.image_url else '사진을 불러오지 못했습니다.')
                item_id = demo_ids.get(row.product_name) if demo_ids is not None else None
                st.button('장바구니에 담기', key=f'card_{product_token(row)}', width='stretch',
                          icon=':material/add_shopping_cart:', on_click=add,
                          args=(row, int(quantity), item_id), help=f'{row.product_name} {quantity}개 담기')
