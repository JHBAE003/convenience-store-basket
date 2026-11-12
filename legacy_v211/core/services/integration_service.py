from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from core.models import StoreProduct
from core.services.product_matching import same_product, same_catalog_reference
from core.services.store_matching import same_store


def integrate_records(inventory_records: Iterable[StoreProduct],
                      price_records: Iterable[StoreProduct]) -> list[StoreProduct]:
    """Join price/event facts only when both product and store identity are safe."""
    prices = list(price_records)
    results = []
    for stock in inventory_records:
        matches = [price for price in prices if same_product(stock, price) and
                   ((not price.store_code and not price.address and not price.store_name)
                    or same_store(stock, price))]
        # 결제 가능한 행사 선택에 실패해도 확인한 목록 가격까지 버리지 않는다.
        # 이름만 일치하는 경로는 참고 가격 전용이며 상품 연결/행사 확정에는 쓰지 않는다.
        references = matches or [price for price in prices if same_catalog_reference(stock, price)]
        reference_price = _select_reference_price(references)
        enriched = stock
        if references:
            observed = [{
                'brand': row.brand, 'product_name': row.product_name,
                'barcode': row.barcode, 'product_code': row.product_code,
                'price': row.price, 'source': row.price_source or row.data_source,
                'source_url': row.promotion_source_url,
                'checked_at': row.promotion_checked_at or row.received_at,
                'source_updated_at': row.price_updated_at,
            } for row in references if row.price > 0]
            enriched = replace(stock, raw_payload={**stock.raw_payload,
                                'reference_price_candidates': observed,
                                'reference_price_match': 'identifier' if matches else 'exact_name_reference_only'})
        if reference_price is not None and stock.price <= 0:
            catalog_only = not reference_price.has_store_identity
            enriched = replace(enriched, price=reference_price.price,
                               price_source=reference_price.price_source or reference_price.data_source,
                               price_updated_at=reference_price.price_updated_at,
                               price_data_status='REFERENCE' if catalog_only or not matches else reference_price.price_data_status)
        price = _select_promotion(matches, stock.price)
        if price is None:
            results.append(enriched)
            continue
        # 점포에서 명시한 조건은 행사명이 같아도 브랜드 목록으로 완화하지 않는다.
        # 이전에 우리가 붙인 브랜드 목록은 점포 원본 조건으로 오인하지 않는다.
        scope = stock.raw_payload.get('promotion_scope')
        catalog_derived = scope == 'brand_catalog' or (
            scope is None and 'promotion_observation' in stock.raw_payload)
        if stock.has_store_identity and stock.promotion_known and stock.promotion and not catalog_derived:
            results.append(enriched)
            continue
        # 브랜드 전체 목록의 판매가는 현재 점포의 확정 판매가를 대체하지 않는다.
        catalog_only = not (price.store_code or price.address or price.store_name)
        use_catalog_price = bool(price.price and not stock.price)
        results.append(replace(enriched, price=(price.price if use_catalog_price else enriched.price), promotion=price.promotion,
                               image_url=stock.image_url or price.image_url,
                               promotion_known=price.promotion_known,
                               price_source=(price.price_source or price.data_source) if use_catalog_price else enriched.price_source,
                               promotion_source=price.promotion_source or price.data_source,
                               promotion_updated_at=price.promotion_updated_at,
                               price_updated_at=price.price_updated_at if use_catalog_price else enriched.price_updated_at,
                               price_data_status=('REFERENCE' if catalog_only else price.price_data_status)
                                                  if use_catalog_price else enriched.price_data_status,
                               promotion_data_status=(price.promotion_data_status if price.promotion_data_status != "UNKNOWN"
                                                      else price.data_source),
                               promotion_start_date=price.promotion_start_date,
                               promotion_end_date=price.promotion_end_date,
                               promotion_buy_quantity=price.promotion_buy_quantity,
                               promotion_gift_quantity=price.promotion_gift_quantity,
                               promotion_member_condition=price.promotion_member_condition,
                               promotion_card_condition=price.promotion_card_condition,
                               promotion_cross_gift=price.promotion_cross_gift,
                               promotion_conditions_verified=price.promotion_conditions_verified,
                               promotion_checked_at=price.promotion_checked_at,
                               promotion_source_url=price.promotion_source_url,
                               raw_payload={**enriched.raw_payload,
                                            'promotion_scope': 'brand_catalog' if catalog_only else 'store',
                                            'promotion_observation': dict(price.raw_payload)}))
    return results


def _select_promotion(matches: list[StoreProduct], stock_price: int) -> StoreProduct | None:
    """Collapse duplicate catalog cards; refuse conflicting prices or unclear offers."""
    if not matches:
        return None
    prices = {row.price for row in matches if row.price > 0}
    if len(prices) > 1:
        return None
    distinct: dict[str, StoreProduct] = {}
    for row in matches:
        label = row.promotion.strip()
        if label not in distinct or row.price > distinct[label].price:
            distinct[label] = row
    offers = list(distinct.values())
    if len(offers) == 1:
        return offers[0]
    comparable = [(replace(row, price=row.price or stock_price).promotion_unit_price, row)
                  for row in offers]
    if any(unit_price is None for unit_price, _ in comparable):
        return None
    return min(comparable, key=lambda item: item[0])[1]


def _select_reference_price(matches: list[StoreProduct]) -> StoreProduct | None:
    """유일한 공개 목록 가격만 선택한다. 다른 가격 중 최저가를 임의로 고르지 않는다."""
    priced = [row for row in matches if row.price > 0]
    if not priced or len({row.price for row in priced}) != 1:
        return None
    return priced[0]
