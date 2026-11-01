import csv
import io
from core.cart_models import QuerySnapshot, StoreEvaluation
from core.security.csv_cells import csv_cell as _cell


def comparison_csv(stores:list[StoreEvaluation],snapshot:QuerySnapshot) -> bytes:
    fields=['매장ID','브랜드','매장','직선거리km','상품ID','상품명','요청수량','재고상태','가격상태',
            '상품결제액','상품참고액','확정총액','확인된상품소계','참고총액','추천가능','데이터상태',
            '상품자료상태','재고','재고수량정확','판매가','행사','가격출처','재고출처','행사출처',
            '가격시각','재고시각','행사자료시각','사유','비교시작시각','비교완료시각',
            '앱수신시각','원본자료시각','앱저장자료사용','행사시작일','행사종료일','행사조건확인']
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=fields)
    writer.writeheader()
    for store in stores:
        for line in store.lines:
            row=line.record
            values=[store.store_key,store.brand,store.store_name,store.distance_km,line.item_id,
                    row.product_name if row else '',line.quantity,line.stock_state,line.pricing_state,
                    line.payable,line.reference_payable,store.total,store.known_subtotal,store.reference_total,
                    '예' if store.eligible else '아니오',store.source,row.data_source if row else '',
                    row.stock if row and row.stock_known else None,row.stock_quantity_exact if row else None,
                    row.price if row and row.price>0 else None,row.promotion if row else '',
                    row.price_source if row else '',row.stock_source if row else '',row.promotion_source if row else '',
                    row.price_updated_at if row else '',row.stock_updated_at if row else '',
                    (row.promotion_updated_at or row.updated_at) if row else '',
                    line.reason+' '+'; '.join(store.reasons),snapshot.requested_at,snapshot.completed_at,
                    row.received_at if row else '',
                    (row.stock_source_updated_at or row.source_updated_at or row.stock_updated_at) if row else '',
                    '예' if row and row.app_cache_used else '아니오',
                    row.promotion_start_date if row else '',row.promotion_end_date if row else '',
                    '예' if row and row.promotion_conditions_verified else '미확인']
            writer.writerow({k:_cell(v) for k,v in zip(fields,values)})
    return ('\ufeff'+stream.getvalue()).encode('utf-8')
