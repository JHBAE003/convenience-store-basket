import csv
from dataclasses import fields
from pathlib import Path
import os
import tempfile
import threading
from core.config import BASE_DIR
from core.cart_models import CartItem, CatalogLink
from core.models import StoreProduct
from .product_matching import barcode_equal, conflicting_variants, normalize_product_name


_LINK_FIELDS = tuple(field.name for field in fields(CatalogLink))
_USER_LINK_LOCK = threading.RLock()


def catalog_link_for(row: StoreProduct, item_id: str) -> CatalogLink:
    """현재 확인한 식별자와 명시 규격을 기존 연결표 형식으로 보존한다."""
    return CatalogLink(**{key: item_id if key == 'item_id' else
                          str(row.raw_payload.get('identifier_source', '')) if key == 'identifier_source' else
                          str(getattr(row, key, '') or '')
                          for key in _LINK_FIELDS})


def _read_links(path: Path) -> list[CatalogLink]:
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'item_id', 'brand', 'barcode', 'product_code', 'product_name'}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError('저장된 상품 연결표의 열을 확인해 주세요.')
        links = []
        for number, row in enumerate(reader, 2):
            if number > 5001 or None in row or any(value is None for value in row.values()):
                raise ValueError('저장된 상품 연결표의 크기나 행 형식이 올바르지 않습니다.')
            if not all((row.get(key) or '').strip() for key in ('item_id', 'brand', 'product_name')):
                raise ValueError('저장된 상품 연결표에 상품명·브랜드·항목 번호가 비어 있습니다.')
            links.append(CatalogLink(**{key: row.get(key, '') for key in _LINK_FIELDS}))
        return links


def load_catalog(path: str | Path | None = None) -> list[CatalogLink]:
    path = Path(path) if path else BASE_DIR / 'sample_data/catalog_links.csv'
    return _read_links(path)


def matches_link(row: StoreProduct, link: CatalogLink) -> bool:
    if row.brand != link.brand:
        return False
    linked = StoreProduct(link.brand, '', '', link.product_name,
                          **{key: getattr(link, key, '') for key in (
                              'barcode', 'product_code', 'manufacturer', 'volume', 'flavor', 'pack_count')
                             if key in {field.name for field in fields(StoreProduct)}})
    if conflicting_variants(row, linked):
        return False
    if link.barcode and row.barcode:
        return barcode_equal(link.barcode, row.barcode)
    if link.product_code and row.product_code:
        return link.product_code.strip() == row.product_code.strip()
    if link.barcode or link.product_code:
        return False
    return bool(link.product_name) and normalize_product_name(row.product_name) == normalize_product_name(link.product_name)


def load_user_catalog(path: str | Path | None = None) -> list[CatalogLink]:
    """이 컴퓨터에서 사용자가 확인한 연결만 읽는다. 시연 연결표와 섞지 않는다."""
    source = Path(path) if path is not None else BASE_DIR / 'user_data' / 'confirmed_links.csv'
    with _USER_LINK_LOCK:
        return _read_links(source) if source.is_file() else []


def save_confirmed_links(links: list[CatalogLink], *, confirmed: bool,
                         path: str | Path | None = None) -> None:
    """명시적 확인 이후에만 기존 CSV 연결표 형식으로 저장한다."""
    if confirmed is not True:
        raise ValueError('상품의 용량·맛·구성이 같다는 확인이 필요합니다.')
    if not links or len({link.item_id for link in links}) != 1:
        raise ValueError('한 장바구니 항목의 연결만 함께 저장할 수 있습니다.')
    if len({link.brand for link in links}) != len(links):
        raise ValueError('한 항목에는 브랜드별 상품을 하나씩만 연결하세요.')
    if any(not link.item_id.strip() or not link.product_name.strip() or not link.brand.strip()
           or not (link.barcode.strip() or link.product_code.strip()) for link in links):
        raise ValueError('다음 조회에서 재사용하려면 상품의 브랜드·이름·식별 코드가 필요합니다.')
    destination = Path(path) if path is not None else BASE_DIR / 'user_data' / 'confirmed_links.csv'
    with _USER_LINK_LOCK:
        previous = load_user_catalog(destination)
        kept = [link for link in previous if link.item_id != links[0].item_id]
        combined = kept + list(links)
        if len(combined) > 5000:
            raise ValueError('저장된 연결이 5,000개를 넘습니다. 연결표를 정리해 주세요.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, scratch = tempfile.mkstemp(prefix='.links-', suffix='.tmp', dir=destination.parent)
        try:
            with os.fdopen(handle, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=_LINK_FIELDS)
                writer.writeheader()
                writer.writerows({key: getattr(link, key) for key in _LINK_FIELDS} for link in combined)
            os.replace(scratch, destination)
        finally:
            if os.path.exists(scratch):
                os.unlink(scratch)


def reusable_links_for(row: StoreProduct, saved_links: list[CatalogLink]) -> list[CatalogLink]:
    """선택한 상품이 저장된 연결의 어느 그룹인지 유일할 때만 그 그룹을 돌려준다."""
    groups = {link.item_id for link in saved_links if matches_link(row, link)}
    if not groups:
        return []
    signatures = {}
    for item_id in groups:
        links = [link for link in saved_links if link.item_id == item_id]
        if len({link.brand for link in links}) != len(links):
            return []
        signatures[item_id] = tuple(sorted(tuple(str(getattr(link, key)) for key in _LINK_FIELDS
                                                  if key != 'item_id') for link in links))
    if len(set(signatures.values())) != 1:
        return []  # 서로 다른 과거 연결을 임의로 고르지 않는다.
    chosen = min(groups)
    return [link for link in saved_links if link.item_id == chosen]


def resolve_cart_records(cart: list[CartItem], records: list[StoreProduct],
                         links: list[CatalogLink]) -> dict[str, list[StoreProduct]]:
    return {item.item_id: [row for row in records if any(
        link.item_id == item.item_id and matches_link(row, link) for link in links
    )] for item in cart}

