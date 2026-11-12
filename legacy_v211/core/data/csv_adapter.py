from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from core.data.base_adapter import DataAdapter
from core.models import StoreProduct


class CsvAdapter(DataAdapter):
    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)

    def search(self, keyword: str, user_lat: float, user_lng: float) -> list[StoreProduct]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"테스트 데이터 파일을 찾을 수 없습니다: {self.csv_path}")
        df = pd.read_csv(self.csv_path, keep_default_na=False, dtype=str)
        key = keyword.strip().lower()
        if key:
            mask = df["product_name"].astype(str).str.lower().str.contains(key, regex=False)
            df = df[mask]

        records: list[StoreProduct] = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in df.to_dict("records"):
            records.append(StoreProduct(
                brand=str(row.get("brand", "")),
                store_name=str(row.get("store_name", "")),
                address=str(row.get("address", "")),
                product_name=str(row.get("product_name", "")),
                barcode=str(row.get("barcode", "")),
                price=int(float(row.get("price", 0) or 0)),
                promotion=str(row.get("promotion", "")),
                stock=int(float(row.get("stock", 0) or 0)),
                latitude=float(row["latitude"]) if str(row.get("latitude", "")).strip() else None,
                longitude=float(row["longitude"]) if str(row.get("longitude", "")).strip() else None,
                data_source="TEST",
                updated_at=str(row.get("updated_at", "")) or now,
                store_code=str(row.get("store_code", "")),
                product_code=str(row.get("product_code", "")),
                image_url=str(row.get("image_url", "")),
                stock_known=_boolean(row.get("stock_known", ""), True),
                stock_quantity_exact=_boolean(row.get("stock_quantity_exact", ""), True),
                promotion_known=_boolean(row.get("promotion_known", ""), True),
                stock_source="시연 CSV",
                price_source="시연 CSV",
                promotion_source="시연 CSV",
                price_updated_at=str(row.get("updated_at", "")) or now,
                stock_updated_at=str(row.get("updated_at", "")) or now,
                promotion_updated_at=str(row.get("updated_at", "")) or now,
                price_data_status="TEST" if int(float(row.get("price", 0) or 0)) > 0 else "UNKNOWN",
                stock_data_status="TEST" if _boolean(row.get("stock_known", ""), True) else "UNKNOWN",
                promotion_data_status="TEST" if _boolean(row.get("promotion_known", ""), True) else "UNKNOWN",
            ))
        return records


def _boolean(value, default):
    if str(value).strip() == '':
        return default
    if str(value).strip().casefold() in ('true','1'):
        return True
    if str(value).strip().casefold() in ('false','0'):
        return False
    raise ValueError(f'잘못된 CSV 참/거짓 값: {value}')
