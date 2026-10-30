from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from core.models import StoreProduct
from core.services.product_matching import same_product
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
        price = _select_promotion(matches, stock.price)
        if price is None:
            results.append(stock)
            continue
        # Store-specific events from the inventory response take precedence over
        # a brand-wide catalog entry when both sources disagree.
        if stock.promotion_known and stock.promotion and stock.promotion != price.promotion:
            results.append(stock)
            continue
        results.append(replace(stock, price=price.price or stock.price, promotion=price.promotion,
                               image_url=stock.image_url or price.image_url,
                               promotion_known=price.promotion_known,
                               price_source=(price.price_source or price.data_source) if price.price else stock.price_source,
                               promotion_source=price.promotion_source or price.data_source,
                               promotion_updated_at=price.promotion_updated_at or price.updated_at,
                               price_updated_at=(price.price_updated_at or price.updated_at) if price.price else stock.price_updated_at,
                               price_data_status=(price.price_data_status if price.price_data_status != "UNKNOWN"
                                                  else price.data_source) if price.price else stock.price_data_status,
                               promotion_data_status=(price.promotion_data_status if price.promotion_data_status != "UNKNOWN"
                                                      else price.data_source)))
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
