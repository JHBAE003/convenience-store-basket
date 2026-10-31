"""일반 사용자가 읽는 확인 범위와 시각. 누락된 원본 시각을 만들지 않는다."""
from datetime import datetime, timedelta, timezone
import streamlit as st

KST = timezone(timedelta(hours=9))
SOURCE_LABELS = {'LIVE': '조회 완료', 'TEST': '시연 자료', 'CACHE': '저장 자료',
                 'PARTIAL': '일부 미확인', 'ERROR': '조회 실패', 'UNKNOWN': '미확인',
                 'REFERENCE': '참고 자료','UNLINKED':'상품 연결 필요'}


def source_label(source):
    return SOURCE_LABELS.get(source, '미확인')


def korean_time(raw):
    if not raw:
        return '미제공'
    try:
        stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return f'{stamp:%Y-%m-%d %H:%M:%S} (시간대 미제공)'
        return stamp.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S 한국 시간')
    except (TypeError, ValueError, AttributeError):
        return '해석할 수 없는 시각'


def render_record_evidence(row):
    st.caption(f'앱이 받은 시각 · {korean_time(row.received_at)}')
    origin = row.stock_source_updated_at or row.source_updated_at or row.stock_updated_at
    st.caption(f'원본 재고 자료 시각 · {korean_time(origin)}')
    if row.app_cache_used or row.data_source == 'CACHE':
        st.caption('앱에 저장된 자료를 사용했습니다. 원본 자료 시각은 바꾸지 않았습니다.')
    else:
        st.caption('앱 저장 자료 사용 안 함 · 중계·원본의 갱신 여부는 제공되는 범위에서만 확인합니다.')
    st.caption(f'가격 출처 · {row.price_source or "미확인"} / 확인 시각 {korean_time(row.price_updated_at)}')
    st.caption(f'재고 출처 · {row.stock_source or "미확인"}')
    st.caption(f'행사 출처 · {row.promotion_source or "미확인"} / 확인 시각 {korean_time(row.promotion_checked_at or row.promotion_updated_at)}')
    if row.promotion:
        start, end = row.promotion_start_date or '미확인', row.promotion_end_date or '미확인'
        st.caption(f'행사 기간 · {start} ~ {end}')
        cross = '가능' if row.promotion_cross_gift is True else ('불가' if row.promotion_cross_gift is False else '미확인')
        st.caption(f'회원 조건 {row.promotion_member_condition} · 카드 조건 {row.promotion_card_condition} · 교차 증정 {cross}')


def render_coverage(snapshot):
    statuses = snapshot.statuses
    total = len(statuses)
    good = sum(s.source in ('LIVE', 'TEST') for s in statuses)
    partial=sum(s.source=='PARTIAL' for s in statuses)
    answered=sum(s.source in ('LIVE','TEST') or (s.source in ('PARTIAL','CACHE') and bool(s.records)) for s in statuses)
    with st.container(border=True):
        st.write(f'**확인 범위 · 자료 응답 {answered}/{total}개 상품·브랜드**')
        st.caption(f'조회 완료 {good} · 일부 확인 {partial} · 실패 {sum(s.source=="ERROR" for s in statuses)} · 미연결 {sum(s.source=="UNLINKED" for s in statuses)}')
        if any(s.source in ('PARTIAL', 'ERROR', 'CACHE') for s in statuses):
            st.caption('일부 자료는 확인하지 못했습니다. 미확인 항목은 확정 추천에 사용하지 않습니다.')
        else:
            st.caption('조회 완료는 결제액 확정을 뜻하지 않습니다. 가격·행사·재고 조건을 따로 확인합니다.')
        unlinked=sorted({s.brand for s in statuses if s.source=='UNLINKED'})
        if unlinked:
            st.caption('아직 같은 상품이 연결되지 않은 브랜드: '+', '.join(unlinked)+' · 장바구니에서 다른 편의점 상품을 연결해 주세요.')
        if any(row.app_cache_used for row in snapshot.records):
            st.caption('앱에 저장된 응답이 포함되어 있습니다. 부분 재조회에서 유지한 브랜드의 수신 시각은 바꾸지 않습니다.')
        st.caption(f'앱의 비교 완료 · {korean_time(snapshot.completed_at)}')


def render_received_observations(snapshot,lat,lng,radius):
    import pandas as pd
    from core.services.observations import observation_rows
    rows=observation_rows(snapshot.records,lat,lng,radius)
    st.subheader('매장별로 받은 가격과 재고')
    st.caption('재고 응답과 참고 판매가를 보여 줍니다. 원본 시각·현재 점포 가격·행사 조건이 미확인이면 확정 추천은 보류합니다.')
    if rows:
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch',height=min(420,80+len(rows)*35),
                     alt='브랜드와 매장별 재고 응답, 참고 가격, 원본 시각과 앱 수신 시각')
        outside=[row for row in rows if row['검색 범위']=='반경 밖']
        if outside:
            st.info(f'반환된 자료 중 {len(outside)}건은 현재 {radius:g}km 반경 밖입니다. 왼쪽 검색 반경을 넓히면 비교 대상으로 볼 수 있습니다.')
    else:
        st.info('이번 조건에서 받은 점포 재고 자료가 없습니다. 아래 브랜드별 상태를 확인해 주세요.')
    display=[]
    for brand in dict.fromkeys(s.brand for s in snapshot.statuses):
        statuses=[s for s in snapshot.statuses if s.brand==brand]
        labels=list(dict.fromkeys(source_label(s.source) for s in statuses))
        count=sum(len(s.records) for s in statuses)
        note=('장바구니에서 같은 상품을 연결하면 조회합니다.' if all(s.source=='UNLINKED' for s in statuses)
              else '조회 실패가 포함되어 있습니다. 다시 조회하거나 상품 연결을 확인해 주세요.' if any(s.source=='ERROR' for s in statuses)
              else ' '.join(dict.fromkeys(s.message for s in statuses if s.source=='PARTIAL')) if any(s.source=='PARTIAL' for s in statuses)
              else '재고 응답과 가격·행사 확인 상태는 별개입니다.')
        display.append({'브랜드':brand,'조회 상태':' · '.join(labels),'반환 점포 자료':count,'안내':note})
    if display:
        st.dataframe(pd.DataFrame(display),hide_index=True,width='stretch',alt='선택한 편의점 브랜드별 실제 조회와 상품 미연결 상태')
