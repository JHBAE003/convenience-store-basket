"""상품 식별자는 종류별로 비교하고, 이름만 있는 자료는 보수적으로 연결한다."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import re

from core.models import StoreProduct


def normalize_product_name(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (value or "").casefold())


def normalized_gtin(value: str) -> str:
    """명시적으로 제공된 GTIN만 검증한다. 내부 코드를 바코드로 추정하지 않는다."""
    code = str(value or "").strip()
    if not re.fullmatch(r"(?:\d{8}|\d{12}|\d{13}|\d{14})", code):
        return ""
    digits = [int(char) for char in code]
    check = (10 - sum(number * (3 if index % 2 == 0 else 1)
                      for index, number in enumerate(reversed(digits[:-1]))) % 10) % 10
    return code.zfill(14) if check == digits[-1] else ""


def barcode_equal(left: str, right: str) -> bool:
    """같은 명시적 바코드 또는 패딩만 다른 유효 GTIN을 비교한다."""
    a, b = str(left or "").strip(), str(right or "").strip()
    if not a or not b:
        return False
    # TEST 자료의 DEMO 식별자와 구형 연결표도 같은 필드 안에서만 비교한다.
    return a == b or bool(normalized_gtin(a) and normalized_gtin(a) == normalized_gtin(b))


_AMOUNT = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>kg|㎏|mg|㎎|ml|㎖|g|ℓ|l)"
    r"(?=$|[^a-z]|x\s*\d|(?:pet|pack|can|bottle)(?=$|[^a-z]|x\s*\d))", re.I)
_PACK = re.compile(
    r"(?:[x×*]\s*(\d+)\s*(?:개입|입|개|팩|봉|병|캔|구)?"
    r"|(\d+)\s*(?:개입|입|팩|봉|병|캔|구|개|packs?|cans?|bottles?)"
    r"|packs?\s*(?:of\s*)?(\d+))(?![가-힣a-z0-9])", re.I)
_MAKER = re.compile(r"^\s*(?:\[([^\]]+)\]|([^()\[\]]+)\))\s*")
_FLAVORS = tuple(sorted(("제로슈거", "무설탕", "오리지널", "스위트칠리", "딸기", "초콜릿", "초코", "바닐라", "녹차", "말차", "바나나", "포도", "복숭아", "레몬", "사과", "망고", "오렌지", "청포도", "자몽", "커피", "치즈", "고구마", "매운맛", "순한맛", "제로", "플레인"), key=len, reverse=True))


def _identity_name(value: str) -> str:
    # +, /, × 같은 미해석 구성 표시는 이름에서 지우지 않는다.
    return re.sub(r"[\s_()\[\]{}.,·]", "", (value or "").casefold())


def _amount(value: str) -> tuple[str, str] | None:
    match = _AMOUNT.search(value or "")
    if match is None:
        return None
    try:
        amount = Decimal(match.group("number"))
    except InvalidOperation:
        return None
    unit = match.group("unit").casefold().replace("㎖", "ml").replace("ℓ", "l").replace("㎏", "kg").replace("㎎", "mg")
    if unit == "l":
        amount, unit = amount * 1000, "ml"
    elif unit == "kg":
        amount, unit = amount * 1000, "g"
    elif unit == "mg":
        amount, unit = amount / 1000, "g"
    return format(amount.normalize(), "f"), unit


@dataclass(frozen=True)
class ProductSignature:
    manufacturer: str
    name: str
    volume: tuple[str, str] | None
    flavor: str
    pack_count: int | None


def product_signature(row: StoreProduct) -> ProductSignature:
    name = row.product_name or ""
    maker_match = _MAKER.match(name)
    maker = str(getattr(row, "manufacturer", "") or "")
    if maker_match:
        maker = maker or maker_match.group(1) or maker_match.group(2)
        name = name[maker_match.end():]
    volume = _amount(str(getattr(row, "volume", "") or "")) or _amount(name)
    raw_pack = getattr(row, "pack_count", None)
    pack = int(raw_pack) if str(raw_pack or "").isdigit() and int(raw_pack) > 0 else None
    pack_match = _PACK.search(name)
    if pack is None and pack_match:
        pack = int(next(group for group in pack_match.groups() if group))
    flavor = str(getattr(row, "flavor", "") or "")
    if not flavor:
        # 이 목록은 연결 근거를 추가하지 않는다. 이름에 명시된 차이를 거절하는 데만 쓴다.
        flavor = next((word for word in _FLAVORS if word.casefold() in name.casefold()), "")
    core_name = _PACK.sub("", _AMOUNT.sub("", name))
    return ProductSignature(normalize_product_name(maker), _identity_name(core_name),
                            volume, normalize_product_name(flavor), pack)


def conflicting_variants(left: StoreProduct, right: StoreProduct) -> bool:
    """공통 식별자가 있더라도 서로 명시된 규격 정보가 충돌하면 연결하지 않는다."""
    for row in (left, right):
        # 개별 필드와 상품명 자체가 충돌하는 응답도 한쪽 값만 골라 신뢰하지 않는다.
        bare = StoreProduct(row.brand, '', '', row.product_name)
        written = product_signature(bare)
        explicit_maker = normalize_product_name(str(getattr(row, 'manufacturer', '') or ''))
        explicit_volume = _amount(str(getattr(row, 'volume', '') or ''))
        explicit_flavor = normalize_product_name(str(getattr(row, 'flavor', '') or ''))
        raw_pack = str(getattr(row, 'pack_count', '') or '')
        explicit_pack = int(raw_pack) if raw_pack.isdigit() and int(raw_pack) > 0 else None
        if any(x and y and x != y for x, y in (
                (written.manufacturer, explicit_maker), (written.volume, explicit_volume),
                (written.flavor, explicit_flavor), (written.pack_count, explicit_pack))):
            return True
    a, b = product_signature(left), product_signature(right)
    return any(x and y and x != y for x, y in (
        (a.manufacturer, b.manufacturer), (a.volume, b.volume),
        (a.flavor, b.flavor), (a.pack_count, b.pack_count)))


_PEPSI_MAKER_LABELS = {'롯데', '칠성', '롯데칠성', '롯데칠성음료'}


def _pepsi_beverage(row: StoreProduct) -> bool:
    signature = product_signature(row)
    label = normalize_product_name(row.product_name)
    # 펩시가 들어간 임의의 굿즈/과자까지 제조사 약칭을 일반화하지 않는다.
    if signature.volume is not None and signature.volume[1] != 'ml':
        return False
    return ('펩시' in label or 'pepsi' in label) and (
        signature.volume is not None or '콜라' in label or 'cola' in label)


def _mapping_pepsi_copy(row: StoreProduct) -> StoreProduct:
    name = row.product_name
    prefix = _MAKER.match(name)
    if prefix and normalize_product_name(prefix.group(1) or prefix.group(2)) in _PEPSI_MAKER_LABELS:
        name = '롯데칠성음료)' + name[prefix.end():]
    explicit = str(row.manufacturer or '')
    if normalize_product_name(explicit) in _PEPSI_MAKER_LABELS:
        explicit = '롯데칠성음료'
    return replace(row, product_name=name, manufacturer=explicit)


def conflicting_mapping_variants(left: StoreProduct, right: StoreProduct) -> bool:
    """사용자가 직접 연결을 확인하는 화면 전용. 자동 상품 매칭에는 쓰지 않는다.

    네 브랜드의 펩시 응답에서 쓰는 롯데/칠성 약칭만 비교용 복제에서 정리한다.
    원본 표기·메타데이터와 same_product의 제조사 기준은 그대로 유지한다.
    """
    if not (_pepsi_beverage(left) and _pepsi_beverage(right)):
        return conflicting_variants(left, right)
    a, b = _mapping_pepsi_copy(left), _mapping_pepsi_copy(right)
    if conflicting_variants(a, b):
        return True
    original_a, original_b = product_signature(left), product_signature(right)
    # 명시된 묶음 수가 한쪽에만 있어도 같은 구성이라고 확인시켜 주지 않는다.
    if (original_a.pack_count or original_b.pack_count) and original_a.pack_count != original_b.pack_count:
        return True
    labels = [normalize_product_name(row.product_name) for row in (left, right)]
    zero = [('제로' in text or 'zero' in text) for text in labels]
    if zero[0] != zero[1]:
        return True
    for local, english in (('라임', 'lime'), ('레몬', 'lemon'), ('망고', 'mango'),
                           ('바닐라', 'vanilla'), ('체리', 'cherry'), ('카페인프리', 'caffeinefree')):
        if (local in labels[0] or english in labels[0]) != (local in labels[1] or english in labels[1]):
            return True
    return False


def same_product(left: StoreProduct, right: StoreProduct) -> bool:
    """같은 브랜드의 같은 상품인지 판단한다. 교차 브랜드 묶기는 사용자가 확인한다."""
    if left.brand != right.brand or conflicting_variants(left, right):
        return False
    if left.barcode and right.barcode:
        return barcode_equal(left.barcode, right.barcode)
    if left.product_code and right.product_code:
        return left.product_code.strip() == right.product_code.strip()
    # barcode와 product_code는 값이 같아 보여도 서로 다른 식별자 공간이다.
    # 식별자를 직접 비교할 수 없으면, 제조사·전체 이름·규격이 모두 일치해야 한다.
    a, b = product_signature(left), product_signature(right)
    return bool(a.name and a.manufacturer and a.volume and a == b)


def shared_gtin(left: StoreProduct, right: StoreProduct) -> bool:
    """브랜드가 다른 후보의 공통 바코드 근거. 가격·행사를 브랜드 간에 합치지는 않는다."""
    code = normalized_gtin(left.barcode)
    return bool(code and code == normalized_gtin(right.barcode)
                and not conflicting_variants(left, right))


def same_catalog_reference(stock: StoreProduct, catalog: StoreProduct) -> bool:
    """같은 전체 이름의 브랜드 목록 가격을 참고로 보여줄 때만 쓰는 별도 판정.

    상품 코드 연결이나 행사·재고의 확정 근거가 아니다. 이미지/카드 식별자는
    재고 서비스의 내부 코드와 다른 공간임을 명시한 수집기만 예외 처리한다.
    """
    if stock.brand != catalog.brand or catalog.has_store_identity or conflicting_variants(stock, catalog):
        return False
    if stock.barcode and catalog.barcode and not barcode_equal(stock.barcode, catalog.barcode):
        return False
    asset_code = catalog.raw_payload.get('identifier_source') in {'official_image_asset', 'official_product_card', 'public_catalog_listing'}
    if stock.product_code and catalog.product_code and stock.product_code != catalog.product_code and not asset_code:
        return False
    a, b = product_signature(stock), product_signature(catalog)
    # 전체 표기가 같아야 한다. 제조사/용량이 생략된 경우에는 미확인 상태를
    # 그대로 두고 참고 목록만 제공하며 코드 연결과 행사 계산에는 참여하지 않는다.
    return bool(a.name and a == b)
