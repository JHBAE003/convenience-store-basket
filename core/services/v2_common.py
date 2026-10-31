"""Shared evidence checks for v2 recommendations; never upgrade unknown facts."""
from datetime import datetime
from dataclasses import replace
from core.cart_models import CartItem, LineEvaluation
from core.services.cart_pricing import evaluate_line


def current_evidence(row) -> bool:
    if row.data_source not in ('TEST', 'LIVE'):
        return False
    statuses = (row.price_data_status, row.stock_data_status, row.promotion_data_status)
    if any(s in ('CACHE', 'ERROR', 'PARTIAL') for s in statuses):
        return False
    if row.data_source == 'TEST':
        return True
    if any(s != 'LIVE' for s in statuses):
        return False
    for raw in (row.price_updated_at or row.updated_at,
                row.stock_updated_at or row.updated_at,
                row.promotion_updated_at or row.updated_at):
        try:
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if stamp.tzinfo is None or not -300 <= (datetime.now().astimezone()-stamp).total_seconds() <= 300:
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
                       stock_state='UNKNOWN',pricing_state='REFERENCE',
                       reason='v2 확인 기준 미충족: 가격·행사·재고의 출처/시각을 다시 확인하세요.')
               if line in invalid else line for line in store.lines]
        amounts=[line.payable if line.payable is not None else line.reference_payable for line in lines]
        result.append(replace(store,lines=lines,total=None,eligible=False,
                              known_subtotal=sum(line.payable or 0 for line in lines),
                              reference_total=sum(amounts) if all(a is not None for a in amounts) else None,
                              reasons=store.reasons+['v2: 현재 확인 가능한 가격·행사·재고 자료가 부족합니다.']))
    return result
