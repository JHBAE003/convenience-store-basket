"""사용자가 확인한 브랜드별 상품을 기존 장바구니 항목에 연결한다."""
from __future__ import annotations

from core.cart_models import CartItem, CatalogLink
from core.models import StoreProduct
from core.services.catalog import catalog_link_for, matches_link
from core.services.product_matching import barcode_equal, conflicting_mapping_variants


def _link_product(link: CatalogLink) -> StoreProduct:
    return StoreProduct(link.brand, '', '', link.product_name,
                        barcode=link.barcode, product_code=link.product_code,
                        manufacturer=link.manufacturer, volume=link.volume,
                        flavor=link.flavor, pack_count=link.pack_count)


def overlaps_active_identity(row: StoreProduct, link: CatalogLink) -> bool:
    """같은 식별자에 상충 표기가 있어도 별도 재고로 중복 사용하지 않는다."""
    if row.brand != link.brand:
        return False
    if row.product_code and link.product_code and row.product_code.strip() == link.product_code.strip():
        return True
    if row.barcode and link.barcode and barcode_equal(row.barcode, link.barcode):
        return True
    return matches_link(row, link)


def attach_confirmed_products(cart: list[CartItem], links: list[CatalogLink], item_id: str,
                              selected: list[StoreProduct], confirmed: bool) -> list[CatalogLink]:
    """새 연결표만 반환한다. 장바구니·수량·입력 연결표를 변경하지 않는다.

    이름이 다른 후보의 연결 근거는 사용자의 명시적 확인이다. 명시된 규격이
    서로 다르거나 다른 활성 장바구니 항목과 겹치면 확인 여부와 관계없이 거절한다.
    """
    targets = [item for item in cart if item.item_id == item_id]
    if len(targets) != 1:
        raise ValueError('연결할 장바구니 상품을 다시 선택해 주세요.')
    if confirmed is not True:
        raise ValueError('선택한 상품의 용량·맛·구성이 같음을 확인해 주세요.')
    if not selected:
        raise ValueError('연결할 브랜드 상품을 하나 이상 선택해 주세요.')
    if any(not row.brand.strip() or not row.product_name.strip() for row in selected):
        raise ValueError('선택한 상품의 브랜드와 이름을 확인해 주세요.')
    brands = {row.brand for row in selected}
    if len(brands) != len(selected):
        raise ValueError('한 장바구니 항목에는 브랜드별 상품을 하나씩만 연결할 수 있습니다.')
    if any(not (row.product_code.strip() or row.barcode.strip()) for row in selected):
        raise ValueError('상품 코드나 바코드가 없는 후보는 연결할 수 없습니다.')

    anchors = [StoreProduct('', '', '', targets[0].label)]
    anchors.extend(_link_product(link) for link in links if link.item_id == item_id)
    if (any(conflicting_mapping_variants(a, b) for index, a in enumerate(selected) for b in selected[index + 1:])
            or any(conflicting_mapping_variants(row, anchor) for row in selected for anchor in anchors)):
        raise ValueError('제조사·용량·맛·포장 구성이 다른 상품은 같은 항목에 연결할 수 없습니다.')

    other_ids = {item.item_id for item in cart if item.item_id != item_id}
    if any(overlaps_active_identity(row, link) for row in selected for link in links if link.item_id in other_ids):
        raise ValueError('다른 장바구니 항목에 이미 담긴 상품입니다. 해당 항목을 먼저 확인해 주세요.')

    kept = [link for link in links if not (link.item_id == item_id and link.brand in brands)]
    result = kept + [catalog_link_for(row, item_id) for row in selected]
    target_brands = [link.brand for link in result if link.item_id == item_id]
    if len(set(target_brands)) != len(target_brands):
        raise ValueError('기존 항목에 같은 브랜드 연결이 중복되어 있습니다. 연결을 먼저 정리해 주세요.')
    return result
