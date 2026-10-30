from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from core.cart_models import CartItem, CatalogLink, QuerySnapshot
from core.models import DataLoadResult
from core.config import TEST_DATA_PATH
from core.data.data_manager import DataManager
from core.data.promotion_manager import PromotionManager


def new_session_manager(mode='TEST'):
    directory=TemporaryDirectory(prefix='basket_')
    root=Path(directory.name)
    promotion=PromotionManager(cache_path=root/'promotions.json')
    manager=DataManager(TEST_DATA_PATH,mode,cache_path=root/'inventory.json',
                        cache_ttl_seconds=300,promotion_manager=promotion)
    return manager,directory


def context_key(cart,lat,lng,brands,mode):
    return (tuple((c.item_id,c.quantity) for c in cart),lat,lng,tuple(sorted(brands)),mode)


def fetch_snapshot(cart:list[CartItem],links:list[CatalogLink],manager:DataManager,
                   user_lat:float,user_lng:float,brands:list[str],mode:str,
                   force_refresh:bool=False) -> QuerySnapshot:
    start=datetime.now().astimezone().isoformat(timespec='seconds')
    statuses=[]
    by_item={}
    records=[]
    queried={}
    for item in cart:
        item_statuses=[]
        item_links=[link for link in links if link.item_id==item.item_id and link.brand in brands]
        for link in item_links:
            key=(link.brand,link.product_name)
            if key not in queried:
                queried[key]=manager.search_all(link.product_name,user_lat,user_lng,[link.brand],mode,
                                                 include_unpriced=True,force_refresh=force_refresh)
                rows,query_statuses=queried[key]
                status_by_brand={s.brand:s.source for s in query_statuses}
                for row in rows:
                    source=status_by_brand.get(row.brand,row.data_source)
                    records.append(replace(row,data_source=source if source in ('PARTIAL','CACHE','ERROR') else row.data_source))
                statuses.extend(query_statuses)
            item_statuses.extend(queried[key][1])
        if not item_links:
            item_statuses.append(DataLoadResult('',[],'ERROR',f'{item.label}: 선택 브랜드에 확인된 상품 대응이 없습니다.'))
            statuses.extend(item_statuses)
        by_item[item.item_id]=item_statuses
    return QuerySnapshot(records,statuses,by_item,context_key(cart,user_lat,user_lng,brands,mode),start,
                         datetime.now().astimezone().isoformat(timespec='seconds'))
