from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Optional


@dataclass
class StoreProduct:
    brand: str
    store_name: str
    address: str
    product_name: str
    barcode: str = ""
    price: int = 0
    promotion: str = ""
    stock: int = 0
    stock_quantity_exact: bool = True
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    distance_km: Optional[float] = None
    data_source: str = "TEST"
    updated_at: str = ""
    store_code: str = ""
    product_code: str = ""
    image_url: str = ""
    stock_source: str = ""
    price_source: str = ""
    promotion_source: str = ""
    price_updated_at: str = ""
    stock_updated_at: str = ""
    stock_known: bool = True
    promotion_known: bool = True
    price_data_status: str = "UNKNOWN"
    stock_data_status: str = "UNKNOWN"
    promotion_data_status: str = "UNKNOWN"
    promotion_updated_at: str = ""
    # 수신 시각과 원본이 제공한 시각은 서로 다른 사실이다.
    received_at: str = ""
    source_updated_at: str = ""
    stock_source_updated_at: str = ""
    app_cache_used: bool = False
    upstream_cache_status: str = ""
    raw_payload: dict = field(default_factory=dict)
    manufacturer: str = ""
    volume: str = ""
    flavor: str = ""
    pack_count: str = ""
    promotion_start_date: str = ""
    promotion_end_date: str = ""
    promotion_buy_quantity: int | None = None
    promotion_gift_quantity: int | None = None
    promotion_member_condition: str = "미확인"
    promotion_card_condition: str = "미확인"
    promotion_cross_gift: bool | None = None
    promotion_conditions_verified: bool = False
    promotion_checked_at: str = ""
    promotion_source_url: str = ""

    @property
    def promotion_unit_price(self) -> Optional[float]:
        promotion = (self.promotion or "").strip().lower()
        if not self.price:
            return None
        if promotion:
            from core.services.promotion import promotion_applicability
            if not promotion_applicability(self)[0]:
                return None
        # When event data is unavailable, show the regular price as the
        # non-promotional effective price; the event label remains unknown.
        if not self.promotion_known:
            return float(self.price)
        if promotion in {"1+1", "1플러스1", "1 plus 1"}:
            return self.price / 2
        if promotion in {"2+1", "2플러스1", "2 plus 1"}:
            return self.price * 2 / 3
        if promotion in {"3+1", "3플러스1", "3 plus 1"}:
            return self.price * 3 / 4
        if promotion in {"세일", "sale"}:
            return float(self.price)
        percent = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*(?:할인)?\s*", promotion)
        if percent and 0 <= float(percent.group(1)) <= 100:
            return self.price * (100 - float(percent.group(1))) / 100
        fixed = re.fullmatch(r"\s*(\d[\d,]*)\s*원\s*할인\s*", promotion)
        if fixed:
            discount = int(fixed.group(1).replace(",", ""))
            return float(self.price - discount) if 0 <= discount <= self.price else None
        if promotion:
            return None
        return float(self.price)

    @property
    def promotion_price_display(self) -> str:
        value = self.promotion_unit_price
        if value is None:
            return "미확인" if not self.promotion_known else "-"
        return f"{round(value):,}원"

    @property
    def stock_display(self) -> str:
        if not self.stock_known:
            return "미확인"
        if self.stock <= 0:
            return "품절"
        if not self.stock_quantity_exact:
            return "재고 있음"
        return f"{self.stock}개"

    @property
    def distance_display(self) -> str:
        if self.distance_km is None:
            return "-"
        if self.distance_km < 1:
            return f"{self.distance_km * 1000:.0f}m"
        return f"{self.distance_km:.2f}km"

    @property
    def price_display(self) -> str:
        return f"{self.price:,}원" if self.price else "-"

    @property
    def has_store_identity(self) -> bool:
        return bool(self.store_name.strip() or self.address.strip() or self.store_code.strip())

    @property
    def store_display(self) -> str:
        return self.store_name.strip() or self.address.strip() or (f"점포코드 {self.store_code}" if self.store_code else "")

    @property
    def updated_display(self) -> str:
        raw = self.updated_at.strip()
        if not raw:
            return "-"
        try:
            updated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return "-"
        if updated.tzinfo is not None:
            updated = updated.astimezone(timezone(timedelta(hours=9)))
        return updated.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DataLoadResult:
    brand: str
    records: list[StoreProduct]
    source: str
    message: str
    cache_age_seconds: float | None = None
    request_count: int = 0
    elapsed_ms: float | None = None
    fallback_used: bool = False
    error_details: dict = field(default_factory=dict)


@dataclass
class Recommendation:
    item: Optional[StoreProduct]
    reason: str
