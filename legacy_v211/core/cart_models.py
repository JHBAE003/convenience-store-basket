from dataclasses import dataclass, field
from .models import StoreProduct, DataLoadResult


@dataclass(frozen=True)
class CartItem:
    item_id: str
    label: str
    quantity: int

    def __post_init__(self):
        if type(self.quantity) is not int or not 1 <= self.quantity <= 10:
            raise ValueError('상품 수량은 1~10개의 정수로 입력하세요.')


@dataclass(frozen=True)
class CatalogLink:
    item_id: str
    brand: str
    barcode: str
    product_code: str
    product_name: str
    manufacturer: str = ''
    volume: str = ''
    flavor: str = ''
    pack_count: str = ''
    identifier_source: str = ''


@dataclass
class LineEvaluation:
    item_id: str
    quantity: int
    payable: int | None
    reference_payable: int | None
    stock_state: str
    pricing_state: str
    reason: str
    record: StoreProduct | None


@dataclass
class StoreEvaluation:
    store_key: str
    brand: str
    store_name: str
    distance_km: float | None
    lines: list[LineEvaluation]
    total: int | None
    known_subtotal: int
    reference_total: int | None
    eligible: bool
    source: str
    reasons: list[str]


@dataclass
class BasketRecommendations:
    cheapest: StoreEvaluation | None
    nearest: StoreEvaluation | None
    over_budget_by: int | None
    message: str


@dataclass
class QuerySnapshot:
    records: list[StoreProduct]
    statuses: list[DataLoadResult]
    statuses_by_item: dict[str, list[DataLoadResult]]
    context_key: tuple
    requested_at: str
    completed_at: str

