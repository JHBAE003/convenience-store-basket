from __future__ import annotations

import re
import math
from dataclasses import dataclass
from typing import Tuple
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def promotion_applicability(record, now: datetime | None = None) -> tuple[bool, str]:
    """Check source-supplied dates on every use, including cached observations.

    Missing dates/conditions are not inferred from the app's receipt time. TEST
    fixtures with no period retain their deliberate, fixed demo contract.
    """
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        raise ValueError("날짜 판단에는 시간대가 있는 시각을 사용하세요.")
    today = current.astimezone(KST).date()
    start = getattr(record, "promotion_start_date", "")
    end = getattr(record, "promotion_end_date", "")
    try:
        start_day = date.fromisoformat(start) if start else None
        end_day = date.fromisoformat(end) if end else None
    except (ValueError, TypeError):
        return False, "행사 기간의 날짜를 확인할 수 없습니다."
    if start_day and end_day and start_day > end_day:
        return False, "행사 시작일과 종료일이 맞지 않습니다."
    if start_day and today < start_day:
        return False, "아직 시작하지 않은 행사입니다."
    if end_day and today > end_day:
        return False, "종료된 행사입니다."
    if not record.promotion_known:
        return False, "행사 여부를 확인하지 못했습니다."
    promo = normalize_promotion(record.promotion)
    if not promo:
        return True, "일반 판매"
    if promo not in {"1+1", "2+1"}:
        return False, "현재 계산을 지원하지 않는 행사입니다."
    if record.data_source == "TEST":
        return True, "시연용 동일 상품 행사"
    if not start_day or not end_day:
        return False, "행사 시작일 또는 종료일이 미확인입니다."
    if not getattr(record, "promotion_conditions_verified", False):
        return False, "회원·카드·증정 조건을 확인하지 못했습니다."
    no_conditions = {"없음", "조건 없음", "해당 없음"}
    if (getattr(record, "promotion_member_condition", "미확인") not in no_conditions
            or getattr(record, "promotion_card_condition", "미확인") not in no_conditions
            or getattr(record, "promotion_cross_gift", None) is not False):
        return False, "회원·카드 또는 교차증정 조건은 계산에 포함하지 않습니다."
    expected = (int(promo[0]), 1)
    if (getattr(record, "promotion_buy_quantity", None),
            getattr(record, "promotion_gift_quantity", None)) != expected:
        return False, "구매·증정 수량을 확인하지 못했습니다."
    return True, "기간과 동일 상품 행사 조건 확인"


def normalize_promotion(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().lower()
    replacements = {
        "1+1": "1+1",
        "1 플러스 1": "1+1",
        "1플러스1": "1+1",
        "2+1": "2+1",
        "2 플러스 1": "2+1",
        "2플러스1": "2+1",
        "3+1": "3+1",
        "3 플러스 1": "3+1",
        "3플러스1": "3+1",
    }
    if text in replacements:
        return replacements[text]
    match = re.fullmatch(r"([123])\s*[+＋]\s*1", text)
    if match:
        return f"{match.group(1)}+1"
    return value.strip()


def effective_unit_price(price: int, promotion: str | None) -> float | None:
    if price <= 0:
        return 0.0
    promo = normalize_promotion(promotion)
    if promo == "1+1":
        return price / 2
    if promo == "2+1":
        return price * 2 / 3
    if promo == "3+1":
        return price * 3 / 4
    percent = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*(?:할인)?\s*", promo)
    if percent:
        amount = float(percent.group(1))
        return price * (100 - amount) / 100 if 0 <= amount <= 100 else None
    fixed = re.fullmatch(r"\s*(\d[\d,]*)\s*원\s*할인\s*", promo)
    if fixed:
        amount = int(fixed.group(1).replace(",", ""))
        return float(price - amount) if 0 <= amount <= price else None
    if not promo:
        return float(price)
    return None


@dataclass(frozen=True)
class PromotionCost:
    payable: int | None
    received: int
    effective_unit_price: float | None
    known: bool
    message: str


def calculate_purchase_cost(price: int, promotion: str | None, quantity: int,
                            stock: int | None = None, stock_known: bool = True) -> PromotionCost:
    """Calculate explicit multi-buy bundles; unknown promotions never become full-price facts."""
    if quantity < 1:
        return PromotionCost(None, 0, None, False, "구매 수량은 1개 이상이어야 합니다.")
    if stock_known and stock is not None and quantity > stock:
        return PromotionCost(None, quantity, None, False, "매장 재고보다 많은 수량입니다.")
    if not price or price < 0:
        return PromotionCost(None, quantity, None, False, "가격을 확인할 수 없습니다.")
    promo = normalize_promotion(promotion)
    if not promo:
        payable = price * quantity
    else:
        bundle = re.fullmatch(r"([123])\+1", promo)
        percent = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*(?:할인)?\s*", promo)
        fixed = re.fullmatch(r"\s*(\d[\d,]*)\s*원\s*할인\s*", promo)
        if bundle:
            paid = int(bundle.group(1))
            bundle_size = paid + 1
            payable = (quantity // bundle_size * paid + quantity % bundle_size) * price
        elif percent and 0 <= float(percent.group(1)) <= 100:
            payable = round(price * (100 - float(percent.group(1))) / 100) * quantity
        elif fixed and 0 <= int(fixed.group(1).replace(",", "")) <= price:
            payable = (price - int(fixed.group(1).replace(",", ""))) * quantity
        else:
            return PromotionCost(None, quantity, None, False, "행사 조건을 해석할 수 없습니다.")
    return PromotionCost(payable, quantity, payable / quantity, True, "계산 완료")


def promotion_summary(price: int, promotion: str | None) -> Tuple[str, str]:
    promo = normalize_promotion(promotion)
    if not promo:
        return "없음", f"{price:,}원/개"
    unit = effective_unit_price(price, promo)
    if unit is None:
        return "미확인", "행사 조건 미확인"
    return promo, f"{round(unit):,}원/개"
