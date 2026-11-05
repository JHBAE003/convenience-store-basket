"""Small fixed TEST fixtures, never observations of real shops."""
from dataclasses import dataclass
from .engine import Product, Store, Offer, Settings

PRODUCTS=(Product('milk','흰 우유','500ml · 무가당 · 1병'),
          Product('snack','감자 과자','60g · 오리지널 · 1봉'),
          Product('water','생수','500ml · 무향 · 1병'))
STORES=(Store('A','CU','CU 시연광장점',37.5674,126.978),
        Store('B','GS25','GS25 시연공원점',37.5737,126.978),
        Store('C','세븐일레븐','세븐일레븐 시연서점점',37.5665,126.9825),
        Store('D','이마트24','이마트24 시연역점',37.5685,126.981))

@dataclass(frozen=True)
class Scenario:
    id: int
    title: str
    description: str
    offers: tuple[Offer,...]
    cart: tuple[tuple[str,int],...]
    settings: Settings
    expected: str

def build(id,title,description,values,cart,expected,budget=10000,strict=False):
    offers=[]
    for s in STORES:
        for p in PRODUCTS:
            raw=values.get((s.id,p.id),(2500,0,''))
            price,stock,promo=(*raw,'')[:3] if len(raw)==2 else raw
            offers.append(Offer(s.id,p.id,price,stock,promo,spec=p.spec))
    return Scenario(id,title,description,tuple(offers),tuple(cart.items()),
                    Settings(budget=budget,strict_budget=strict),expected)

SCENARIOS=(
 build(1,'한 곳 구매가 유리','가까운 CU에서 모든 상품을 가장 싸게 구매합니다.',
       {(s.id,p.id):(500 if s.id=='A' else 2000,8,'') for s in STORES for p in PRODUCTS},
       {'milk':1,'snack':1,'water':1},'CU A 한 곳, 결제액 1,500원.'),
 build(2,'먼 매장이 더 저렴','가까운 CU와 먼 GS25의 가격·거리 차이를 비교합니다.',
       {(s.id,p.id):(1500 if s.id=='A' else 800 if s.id=='B' else 2200,8,'') for s in STORES for p in PRODUCTS},
       {'milk':1,'snack':1},'최저 결제액 GS25 B 1,600원 / 최단 이동 CU A 3,000원.'),
 build(3,'두 곳으로 재고 해결','우유와 과자를 서로 다른 지점에서 구매해야 합니다.',
       {('A','milk'):(1000,2,''),('B','snack'):(800,2,'')},
       {'milk':2,'snack':2},'A+B 2곳, 결제액 3,600원. 1곳 구매 불가.'),
 build(4,'세 곳 방문이 필수','세 품목의 재고가 각각 다른 매장에만 있습니다.',
       {('A','milk'):(1000,1,''),('B','snack'):(800,1,''),('C','water'):(500,1,'')},
       {'milk':1,'snack':1,'water':1},'A+B+C 3곳, 2,300원. 최대 방문 2곳으로 바꾸면 불가.'),
 build(5,'같은 품목 수량 분할','우유 5개를 CU 2개와 GS25 3개로 나눕니다.',
       {('A','milk'):(1000,2,''),('B','milk'):(800,3,'')},
       {'milk':5},'A 2개+B 3개, 결제액 4,400원. 증정품도 재고에서 차감.'),
 build(6,'행사와 수량의 역전','우유 2개와 3개의 최저 구매처가 바뀝니다.',
       {('A','milk'):(1200,12,'1+1'),('B','milk'):(1000,12,'2+1')},
       {'milk':3},'3개는 B 2,000원. 수량을 2개로 바꾸면 A 1,200원.'),
 build(7,'가격과 이동의 선택','최저가·최단 이동·균형 기준을 비교합니다.',
       {('A','milk'):(1500,6,''),('B','milk'):(700,6,''),('C','milk'):(1000,6,'')},
       {'milk':2},'최저 B 1,400원 / 최단 A 3,000원. 부담값에 따라 균형 추천 변경.'),
 build(8,'예산·재고 모두 부족','전체 재고가 부족하고 예산도 낮습니다. 조건을 바꿔 원인을 확인합니다.',
       {('A','milk'):(1000,1,''),('B','milk'):(800,1,'')},
       {'milk':5},'확인 재고 2개, 3개 부족. 예산 5,000원으로 올리면 일부 구매안 1,800원.',budget=100,strict=True),
)

def get_scenario(id):return next(s for s in SCENARIOS if s.id==id)
