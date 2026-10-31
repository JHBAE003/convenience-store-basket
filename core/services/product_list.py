"""Product browsing is separate from store inventory and basket eligibility."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from core.config import BASE_DIR
from core.models import StoreProduct
from core.services.product_matching import normalize_product_name, same_product, product_signature


def product_key(row: StoreProduct) -> tuple:
    return row.brand, row.barcode, row.product_code, row.product_name, product_signature(row)


def product_token(row: StoreProduct) -> str:
    return hashlib.sha256(repr(product_key(row)).encode('utf-8')).hexdigest()[:16]


def load_photo_catalog(path: Path | None = None) -> list[StoreProduct]:
    path = path or BASE_DIR / 'sample_data' / 'photo_catalog.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    return [StoreProduct(
        brand=item['brand'], store_name='', address='', product_name=item['product_name'],
        barcode=item['barcode'], product_code=item['product_code'], price=item['price'],
        image_url=item['image_url'], updated_at=item['observed_at'], data_source='CATALOG',
        stock_known=False, promotion_known=False, price_source=item['source_url'],
        manufacturer=item.get('manufacturer', ''), volume=item.get('volume', ''),
        flavor=item.get('flavor', ''), pack_count=item.get('pack_count', ''),
        price_data_status='REFERENCE',
        raw_payload={'identifier_source': item.get('identifier_source', 'official_image_asset'),
                     'catalog_only': True, 'image_path': item.get('image_path', ''),
                     'source_url': item['source_url'], 'catalog_checked_at': item['observed_at']},
    ) for item in data]


def list_products(rows: list[StoreProduct], keyword: str = '', brand: str = '전체',
                  sort: str = '기본순') -> list[StoreProduct]:
    """Collapse repeated stores without conflating brands or package variants."""
    query = normalize_product_name(keyword)
    grouped: dict[tuple, StoreProduct] = {}
    for row in rows:
        if brand != '전체' and row.brand != brand:
            continue
        if query and query not in normalize_product_name(row.product_name):
            continue
        key = product_key(row)
        if key not in grouped:
            grouped[key] = row
        elif not grouped[key].image_url and row.image_url:
            grouped[key] = replace(grouped[key], image_url=row.image_url)
    products = list(grouped.values())
    if sort == '이름순':
        products.sort(key=lambda p: (p.product_name, p.brand))
    return products


def local_photo(row: StoreProduct) -> Path | None:
    """Only use the exact catalog product's bundled photo, never a fuzzy match."""
    if row.data_source == 'TEST':
        return None
    for candidate in load_photo_catalog():
        if same_product(row, candidate):
            for suffix in ('.jpg', '.png'):
                path = BASE_DIR / 'assets' / 'products' / (candidate.barcode + suffix)
                if path.is_file():
                    return path
    return None
