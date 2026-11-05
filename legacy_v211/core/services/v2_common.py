"""Shared evidence checks for v2 recommendations; never upgrade unknown facts."""
from datetime import datetime
from dataclasses import replace
from core.cart_models import CartItem, LineEvaluation
from core.services.cart_pricing import evaluate_line
from core.services.promotion import promotion_applicability


def current_evidence(row) -> bool:
    if row.data_source not in ('TEST', 'LIVE'):
        return False
    statuses = (row.price_data_status, row.stock_data_status, row.promotion_data_status)
    if any(s in ('CACHE', 'ERROR', 'PARTIAL') for s in statuses):
        return False
    if row.data_source == 'TEST':
        return promotion_applicability(row)[0]
    if any(s != 'LIVE' for s in statuses):
        return False
    if not promotion_applicability(row)[0]:
        return False
    # v2.1 응답의 수신 시각은 원본 재고 시각을 대신할 수 없다.
    if row.received_at and not (row.stock_source_updated_at or row.source_updated_at or row.stock_updated_at):
        return False
    origin_stock = row.stock_source_updated_at or row.source_updated_at or row.stock_updated_at
    checks = ((row.price_updated_at or row.updated_at, 300),
              (origin_stock if row.received_at else (origin_stock or row.updated_at), 300),
              (row.promotion_checked_at or row.promotion_updated_at or row.updated_at,
               21600 if row.promotion_checked_at else 300))
    for raw, max_age in checks:
        try:
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            # 행사 목록은 6시간 캐시여도 매번 행사 기간/조건을 다시 검증한다.
            if stamp.tzinfo is None or not -300 <= (datetime.now().astimezone()-stamp).total_seconds() <= max_age:
                return False
        except (ValueError, TypeError, AttributeError):
            return False
    return True


def confirmed_cost(line: LineEvaluation, quantity: int) -> int | None:
    if quantity == 0:
        return 0
    if line.record is None or line.pricing_state != 'CONFIRMED':
        return None
    if line.stock_state not in ('ENOUGH', 'SHORT') or not current_evidence(line.record):
        return None
    checked = evaluate_line(CartItem(line.item_id, line.record.product_name, quantity), line.record)
    if checked.stock_state != 'ENOUGH' or checked.pricing_state != 'CONFIRMED':
        return None
    return checked.payable


def apply_current_evidence(stores):
    """Use v2's same evidence policy in the headline and detailed comparisons."""
    result=[]
    for store in stores:
        invalid=[line for line in store.lines if line.record is not None and not current_evidence(line.record)]
        if not invalid:
            result.append(store)
            continue
        lines=[replace(line,payable=None,
                       reference_payable=line.payable if line.payable is not None else line.reference_payable,
                       pricing_state='REFERENCE',
                       reason=line.reason+' 확정 비교에 필요한 가격·행사·재고의 시각 또는 조건이 미확인입니다.')
               if line in invalid else line for line in store.lines]
        amounts=[line.payable if line.payable is not None else line.reference_payable for line in lines]
        result.append(replace(store,lines=lines,total=None,eligible=False,
                              known_subtotal=sum(line.payable or 0 for line in lines),
                              reference_total=sum(amounts) if all(a is not None for a in amounts) else None,
                              reasons=store.reasons+['현재 확인 가능한 가격·행사·재고 자료가 부족합니다.']))
    return result
