"""수집된 응답을 표시한다. 가격/수량 응답은 확정 구매 추천과 구분한다."""
from datetime import datetime, timedelta, timezone
from core.services.distance import haversine_km
from core.services.promotion import promotion_applicability


def timestamp_label(raw):
    if not raw:
        return '미제공'
    try:
        value=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if value.tzinfo is None:
            return value.strftime('%m-%d %H:%M:%S')+' (시간대 미제공)'
        return value.astimezone(timezone(timedelta(hours=9))).strftime('%m-%d %H:%M:%S')
    except (ValueError,TypeError,AttributeError):
        return '시각 해석 불가'


def observation_rows(records,lat,lng,radius):
    rows=[]
    for record in records:
        distance=haversine_km(lat,lng,record.latitude,record.longitude)
        if not record.stock_known:
            stock='미확인'
        elif record.stock<=0:
            stock='0개 · 품절 응답'
        elif not record.stock_quantity_exact:
            stock='재고 있음 · 수량 미확인'
        else:
            stock=f'{record.stock}개'
        if record.data_source=='CACHE' or record.stock_data_status=='CACHE':
            stock='저장 자료 · '+stock
        if record.data_source=='TEST':
            price_kind='시연 가격'
        elif record.price_data_status=='LIVE':
            price_kind='점포 응답 가격'
        else:
            price_kind='상품 목록 참고가' if record.price else '미제공'
        allowed,reason=promotion_applicability(record)
        event=(record.promotion or '일반 판매 확인') if allowed else (record.promotion+' · 조건 확인 필요' if record.promotion else '미확인')
        if record.promotion and not allowed and '종료' in reason:
            event=record.promotion+' · 종료(적용 제외)'
        rows.append({'브랜드':record.brand,'매장':record.store_display or '점포 미확인',
                     '상품':record.product_name,'재고 응답':stock,
                     '판매가':f'{record.price:,}원' if record.price>0 else '미제공',
                     '가격 구분':price_kind,'행사':event,
                     '직선거리':f'{distance:.2f}km' if distance is not None else '위치 미확인',
                     '검색 범위':('반경 안' if distance<=radius else '반경 밖') if distance is not None else '위치 미확인',
                     '원본 재고 시각':timestamp_label(record.stock_source_updated_at or record.source_updated_at or record.stock_updated_at),
                     '앱 수신 시각':timestamp_label(record.received_at),
                     '저장 자료': '사용' if record.app_cache_used else '미사용'})
    return rows
