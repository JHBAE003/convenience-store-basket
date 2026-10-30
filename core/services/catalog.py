import csv
from pathlib import Path
from core.config import BASE_DIR
from core.cart_models import CartItem, CatalogLink
from core.models import StoreProduct
from .product_matching import normalize_product_name


def load_catalog(path: str | Path | None = None) -> list[CatalogLink]:
    path = Path(path) if path else BASE_DIR / 'sample_data/catalog_links.csv'
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return [CatalogLink(**row) for row in csv.DictReader(stream)]


def matches_link(row: StoreProduct, link: CatalogLink) -> bool:
    if row.brand != link.brand:
        return False
    if link.barcode and row.barcode:
        return link.barcode.strip() == row.barcode.strip()
    if link.product_code and row.product_code:
        return link.product_code.strip() == row.product_code.strip()
    if link.barcode or link.product_code:
        return False
    return bool(link.product_name) and normalize_product_name(row.product_name) == normalize_product_name(link.product_name)


def resolve_cart_records(cart: list[CartItem], records: list[StoreProduct],
                         links: list[CatalogLink]) -> dict[str, list[StoreProduct]]:
    return {item.item_id: [row for row in records if any(
        link.item_id == item.item_id and matches_link(row, link) for link in links
    )] for item in cart}

