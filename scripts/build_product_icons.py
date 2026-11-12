"""300종의 정규화된 상품 자료에서 작고 독립적인 SVG를 만든다."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from planner.test_data import TestDataset
from planner.product_art import ICON_DIR,product_svg,GENERIC_SVG


def build():
    ICON_DIR.mkdir(parents=True,exist_ok=True)
    with TestDataset().connect() as db:
        products=[dict(r) for r in db.execute('SELECT * FROM products ORDER BY id')]
    for p in products:(ICON_DIR/(p['id']+'.svg')).write_text(product_svg(p),encoding='utf-8')
    (ICON_DIR/'generic.svg').write_text(GENERIC_SVG,encoding='utf-8')
    return len(products)


if __name__=='__main__':print('상품 그림 생성:',build(),'종')
