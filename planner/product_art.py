"""가상 상품 구분용 로컬 그림. 실제 상품 사진이나 재고 증거가 아니다."""
from html import escape
from pathlib import Path
import re

ICON_DIR=Path(__file__).resolve().parents[1]/'assets/product_icons'
COLORS=('#3a8568','#cb6879','#9a7539','#7785ba','#b97f47')


def icon_path(product_id):
    # 외부 상품 코드가 파일 경로가 되지 않게 한다.
    if re.fullmatch(r'TEST-P\d{3}',product_id or ''):
        path=ICON_DIR/(product_id+'.svg')
        if path.is_file():return path
    return ICON_DIR/'generic.svg'


def product_svg(product):
    index=int(product['id'][-3:])-1
    color=COLORS[(index%25)//5]
    category=product['category'];flavor=escape(product['flavor']);volume=escape(product['volume'])
    if category=='우유':
        shape='<path d="M91 69 111 46h50l20 23v120H91z"/><path d="M91 69h90M111 46v23" fill="none"/>'
    elif category in ('음료','생수'):
        shape='<rect x="116" y="39" width="40" height="17" rx="4"/><path d="M116 56v19l-18 23v80q0 13 14 13h48q14 0 14-13V98l-18-23V56z"/>'
    elif category in ('컵라면','커피'):
        shape='<path d="m88 86 14 104h68l14-104z"/><rect x="82" y="73" width="108" height="16" rx="7"/>'
    elif category=='샌드위치':
        shape='<path d="M75 186 136 59l61 127z"/><path d="m90 177 46-96 46 96z" fill="white"/>'
    elif category in ('도시락','즉석밥'):
        shape='<rect x="66" y="91" width="140" height="91" rx="20"/><path d="M76 104h120M147 104v64" fill="none"/>'
    elif category=='아이스크림':
        shape='<rect x="103" y="51" width="66" height="119" rx="27"/><path d="M126 170v27h20v-27" fill="#dac7a9"/>'
    elif category=='초콜릿':
        shape='<rect x="91" y="60" width="90" height="130" rx="9"/><path d="M101 85h70M101 112h70M121 70v52M151 70v52" fill="none"/>'
    else:
        shape='<path d="m85 67 10-14h82l10 14-7 113 7 14H85l7-14z"/><path d="M95 66h82M95 181h82" fill="none"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 272 260" role="img" aria-label="{escape(product["name"],quote=True)} 구분용 그림">'
            f'<rect width="272" height="260" rx="24" fill="#f3f6f2"/><ellipse cx="136" cy="200" rx="68" ry="8" fill="#dfe8dd"/>'
            f'<g fill="{color}" stroke="{color}" stroke-width="3" stroke-linejoin="round">{shape}</g>'
            '<rect x="94" y="123" width="84" height="38" rx="8" fill="white"/>'
            f'<g font-family="sans-serif" text-anchor="middle" fill="#263c32"><text x="136" y="147" font-size="16" font-weight="700">{volume}</text>'
            f'<text x="136" y="226" font-size="13">{flavor}</text><text x="136" y="246" font-size="10" fill="#65756a">가상 상품 · {escape(product["id"])} · {product["pack_count"]}개 구성</text></g></svg>')


GENERIC_SVG='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 272 260" role="img" aria-label="상품 공통 그림"><rect width="272" height="260" rx="24" fill="#f3f6f2"/><rect x="83" y="70" width="106" height="116" rx="18" fill="#658c76"/><path d="M112 72V60a24 24 0 0 1 48 0v12" fill="none" stroke="#658c76" stroke-width="10"/><text x="136" y="226" text-anchor="middle" font-family="sans-serif" font-size="14" fill="#263c32">상품 공통 그림</text></svg>'
