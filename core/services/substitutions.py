"""Explicit, curated alternatives with independently confirmed purchase evidence.

The bundled catalog only describes fictional demonstration products. A name or a
search keyword never establishes that two real products are interchangeable.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path

from core.cart_models import CartItem
from core.config import BASE_DIR
from core.models import StoreProduct
from .cart_pricing import evaluate_line
from .product_matching import same_product
from .store_matching import same_store


@dataclass(frozen=True)
class SubstituteProduct:
    brand: str
    barcode: str
    product_code: str
    product_name: str
    group: str
    amount: float
    unit: str


@dataclass(frozen=True)
class SubstituteOption:
    record: StoreProduct
    payable: int
    extra_cost: int
    volume_ratio: float
    reason: str


def load_substitute_catalog(path: str | Path | None = None) -> list[SubstituteProduct]:
    """Load explicit metadata, without inferring groups from product names."""
    source = Path(path) if path is not None else BASE_DIR / 'sample_data/substitute_catalog.csv'
    with source.open(encoding='utf-8-sig', newline='') as stream:
        return [SubstituteProduct(**{**row, 'amount': float(row['amount'])})
                for row in csv.DictReader(stream)]


def _identified(row: StoreProduct) -> bool:
    return bool(row.brand.strip() and (row.barcode.strip() or row.product_code.strip()))


def _metadata(row: StoreProduct, catalog: list[SubstituteProduct]) -> SubstituteProduct | None:
    if not _identified(row):
        return None
    found = []
    for entry in catalog:
        if not (entry.barcode.strip() or entry.product_code.strip()):
            continue
        identity = StoreProduct(entry.brand, '', '', entry.product_name,
                                barcode=entry.barcode, product_code=entry.product_code)
        if same_product(row, identity):
            found.append(entry)
    # Ambiguous metadata is not resolved by picking the first CSV row.
    facts = {(entry.group.strip(), entry.amount, entry.unit.strip().casefold()) for entry in found}
    if len(facts) != 1:
        return None
    entry = found[0]
    return entry if (entry.group.strip() and entry.unit.strip()
                     and math.isfinite(entry.amount) and entry.amount > 0) else None


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # LIVE observations must carry an unambiguous source timezone. TEST
        # fixtures can retain the local, fixed observation time used by CSVs.
        return parsed
    except (ValueError, TypeError, AttributeError):
        return None


def _evidence(row: StoreProduct, field: str, now: datetime) -> bool:
    if row.data_source not in ('TEST', 'LIVE'):
        return False
    if getattr(row, f'{field}_data_status') != row.data_source:
        return False
    if not getattr(row, f'{field}_source').strip():
        return False
    observed = _parse_time(getattr(row, f'{field}_updated_at'))
    if observed is None:
        return False
    if row.data_source == 'TEST':
        return True  # Fixed fictional observations are not live stock claims.
    if observed.tzinfo is None:
        return False
    age = (now - observed.astimezone(timezone.utc)).total_seconds()
    return -300 <= age <= 300


def _located_store(row: StoreProduct) -> bool:
    if not row.brand.strip() or not (row.store_code.strip() or row.address.strip()):
        return False
    lat, lng = row.latitude, row.longitude
    if lat is None or lng is None:
        return False
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return False
    return row.distance_km is None or (math.isfinite(row.distance_km) and row.distance_km >= 0)


def _observation_time(row: StoreProduct) -> datetime:
    observed = _parse_time(row.stock_updated_at or row.updated_at)
    if observed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)


def _facts(row: StoreProduct) -> tuple:
    return (row.price, row.promotion, row.promotion_known, row.stock,
            row.stock_known, row.stock_quantity_exact, row.data_source,
            row.price_data_status, row.stock_data_status, row.promotion_data_status,
            row.price_source, row.stock_source, row.promotion_source,
            row.price_updated_at, row.stock_updated_at, row.promotion_updated_at,
            row.latitude, row.longitude, row.address, row.store_code)


def _latest(rows: list[StoreProduct]) -> StoreProduct | None:
    latest_time = max(_observation_time(row) for row in rows)
    latest = [row for row in rows if _observation_time(row) == latest_time]
    if len({_facts(row) for row in latest}) != 1:
        return None
    return min(latest, key=lambda row: (_identity_order(row),
               row.distance_km if row.distance_km is not None and math.isfinite(row.distance_km)
               else math.inf))


def _identity_order(row: StoreProduct) -> tuple:
    return (row.brand, row.store_code, row.address, row.store_name,
            row.barcode, row.product_code, row.product_name)


def _same_observation(left: StoreProduct, right: StoreProduct) -> bool:
    return _identified(left) and _identified(right) and same_store(left, right) and same_product(left, right)


def _deduplicated(rows: list[StoreProduct]) -> list[StoreProduct]:
    groups: list[list[StoreProduct]] = []
    for row in sorted(rows, key=_identity_order):
        matching = [group for group in groups if any(_same_observation(row, other) for other in group)]
        if not matching:
            groups.append([row])
        else:
            merged = [row]
            for group in matching:
                merged.extend(group)
                groups.remove(group)
            groups.append(merged)
    return [selected for group in groups if (selected := _latest(group)) is not None]


def _original_product(original: StoreProduct, candidate: StoreProduct) -> bool:
    # Barcodes identify the original even when another chain sells it.
    if original.barcode.strip() and original.barcode.strip() == candidate.barcode.strip():
        return True
    return _identified(original) and _identified(candidate) and same_product(original, candidate)


def find_substitutes(original: StoreProduct, candidates: list[StoreProduct], quantity: int, *,
                     catalog: list[SubstituteProduct] | None = None, max_extra: int = 0,
                     volume_tolerance: float = 0.25) -> list[SubstituteOption]:
    """Return confirmed alternatives within an explicit group, unit and cost ceiling.

    Extra cost compares the same requested quantity and supported promotion of
    each product. Original stock may be insufficient; its confirmed price and
    promotion are still required. TEST evidence remains explicitly fictional.
    """
    item = CartItem('substitute', original.product_name, quantity)
    if type(max_extra) is not int or max_extra < 0:
        raise ValueError('추가 지출 한도는 0 이상의 정수로 입력하세요.')
    if not math.isfinite(volume_tolerance) or volume_tolerance < 0:
        raise ValueError('용량 허용 오차는 0 이상의 유한한 수로 입력하세요.')
    entries = load_substitute_catalog() if catalog is None else catalog
    original_rows = [original] + [row for row in candidates if _same_observation(original, row)]
    confirmed_original = _latest(original_rows)
    if confirmed_original is None:
        return []
    original_meta = _metadata(confirmed_original, entries)
    now = datetime.now(timezone.utc)
    if (original_meta is None or not _evidence(confirmed_original, 'price', now)
            or not _evidence(confirmed_original, 'promotion', now)):
        return []
    baseline = evaluate_line(item, confirmed_original)
    if baseline.pricing_state != 'CONFIRMED' or baseline.payable is None:
        return []
    options = []
    for candidate in _deduplicated(candidates):
        if _original_product(confirmed_original, candidate):
            continue
        metadata = _metadata(candidate, entries)
        if (metadata is None or metadata.group.strip() != original_meta.group.strip()
                or metadata.unit.strip().casefold() != original_meta.unit.strip().casefold()):
            continue
        ratio = metadata.amount / original_meta.amount
        if not 1 - volume_tolerance <= ratio <= 1 + volume_tolerance:
            continue
        if not _located_store(candidate) or not all(_evidence(candidate, field, now)
                                                    for field in ('price', 'promotion', 'stock')):
            continue
        line = evaluate_line(item, candidate)
        if line.stock_state != 'ENOUGH' or line.pricing_state != 'CONFIRMED' or line.payable is None:
            continue
        extra_cost = line.payable - baseline.payable
        if extra_cost > max_extra:
            continue
        reason = (f'명시적 대체 그룹 {metadata.group} · 용량 {ratio:.0%} · '
                  f'{quantity}개 {line.payable:,}원 · 원상품 대비 {extra_cost:+,}원')
        options.append(SubstituteOption(candidate, line.payable, extra_cost, ratio, reason))
    return sorted(options, key=lambda option: (option.payable,
                  option.record.distance_km if option.record.distance_km is not None else math.inf,
                  _identity_order(option.record)))
