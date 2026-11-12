from core.cart_models import CartItem, LineEvaluation
from core.models import StoreProduct
from .promotion import calculate_purchase_cost, normalize_promotion, promotion_applicability
from datetime import datetime


def evaluate_line(item: CartItem, record: StoreProduct | None) -> LineEvaluation:
    if record is None:
        return LineEvaluation(item.item_id,item.quantity,None,None,'UNKNOWN','UNKNOWN','이 매장의 상품 관측 자료가 없습니다.',None)
    reasons=[]
    stale=False
    if record.data_source=='LIVE':
        try:
            raw = record.stock_source_updated_at or record.source_updated_at or record.stock_updated_at
            if not raw and not record.received_at:
                raw = record.updated_at  # Legacy fixtures explicitly carry their observation here.
            observed=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if observed.tzinfo is None:
                raise ValueError('재고 원본 관측 시각의 시간대 미확인')
            age=(datetime.now().astimezone()-observed).total_seconds()
            stale=not -300<=age<=300
        except (ValueError,TypeError):
            stale=True
    if not record.stock_known or not record.stock_quantity_exact or stale or record.stock_data_status in ('ERROR','CACHE'):
        stock='UNKNOWN'
        reasons.append('현재 필요 수량을 확인할 수 없습니다.' if not stale else '재고 관측 시각이 미확인이거나 유효 범위(5분)를 벗어났습니다.')
    elif record.stock < item.quantity:
        stock='SHORT'
        reasons.append(f'재고 {record.stock}개로 {item.quantity}개 구매에 부족합니다.')
    else:
        stock='ENOUGH'
    payable=reference=None
    state='UNKNOWN'
    promo=normalize_promotion(record.promotion)
    if record.price <= 0:
        reasons.append('가격 미확인')
    elif not promotion_applicability(record)[0] or promo not in ('','1+1','2+1'):
        reference=record.price*item.quantity
        state='REFERENCE'
        reasons.append(promotion_applicability(record)[1] + ': 일반가 참고액')
    elif (record.price_data_status in ('CACHE','ERROR','REFERENCE','PARTIAL')
          or record.promotion_data_status in ('CACHE','ERROR','REFERENCE','PARTIAL')
          or (record.data_source == 'LIVE'
              and (record.price_data_status != 'LIVE' or record.promotion_data_status != 'LIVE'))):
        reference=calculate_purchase_cost(record.price,promo,item.quantity,stock_known=False).payable
        state='REFERENCE'
        reasons.append('현재 매장에서 확인되지 않은 가격·행사 자료: 참고액')
    else:
        payable=calculate_purchase_cost(record.price,promo,item.quantity,stock_known=False).payable
        state='CONFIRMED' if payable is not None else 'UNKNOWN'
    return LineEvaluation(item.item_id,item.quantity,payable,reference,stock,state,' '.join(reasons) or '필요 수량과 금액 확인',record)

