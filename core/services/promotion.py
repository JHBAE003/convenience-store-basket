from __future__ import annotations

import re
import math
from dataclasses import dataclass
from typing import Tuple


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
    match = re.search(r"([123])\s*[+＋]\s*1", text)
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
