from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Any

import requests

from core.config import REQUEST_TIMEOUT_SECONDS

from core.data.live_mcp_adapter import DaisoMcpAdapter, LiveDataError
from core.data.mcp_client import McpClient, McpRequestError
from core.models import StoreProduct
from core.services.geocoding import emart24_region_korean, reverse_geocode_korean
from core.services.product_matching import same_product
from core.services.store_matching import same_store


class CompositeInventoryAdapter:
    """Second inventory track using official stock APIs and documented MCP tools."""

    TRACK_NAME = "Daiso MCP 통합조회"

    def __init__(self, brand: str, mcp_client: McpClient | None = None,
                 geocoder=reverse_geocode_korean,
                 emart_region: tuple[str, str] | None = None):
        if brand not in {"GS25", "CU", "세븐일레븐", "이마트24"}:
            raise ValueError(f"통합 조회 경로를 지원하지 않는 브랜드: {brand}")
        self.brand = brand
        self.mcp = mcp_client or McpClient()
        self.geocoder = geocoder
        self.lookup_failures: list[str] = []
        self.catalog_incomplete = False
        self.emart_region = emart_region

    def search(self, keyword: str, user_lat: float, user_lng: float) -> list[StoreProduct]:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            if self.brand == "CU":
                return self._search_cu(keyword, user_lat, user_lng, now)
            if self.brand == "GS25":
                products_result = self.mcp.call("gs25_search_products", {
                    "keyword": keyword,
                    "pageSize": DaisoMcpAdapter.MAX_PRODUCTS,
                })
                products = DaisoMcpAdapter._dict_list(products_result.get("products"))
                candidates = DaisoMcpAdapter._matching_products(
                    products, keyword, ("itemName", "name", "shortItemName"), ("itemCode", "code")
                )
                def lookup(product):
                    code = str(DaisoMcpAdapter._first(product, "itemCode", "code"))
                    result = self.mcp.call("gs25_check_inventory", {
                        "keyword": keyword, "itemCode": code,
                        "latitude": user_lat, "longitude": user_lng, "storeLimit": 20,
                    })
                    return [row for row in self._parse_gs25(result, keyword, now, product)
                            if row.product_code == code]
                return DaisoMcpAdapter._collect_inventory(candidates, lookup, self.lookup_failures)
            if self.brand == "이마트24":
                region = self.emart_region or emart24_region_korean(user_lat, user_lng, self.geocoder)
                adapter = DaisoMcpAdapter("이마트24", mcp_client=self.mcp, emart_region=region)
                candidates = adapter._emart_products(keyword)
                self.catalog_incomplete = adapter.catalog_incomplete
                if not candidates:
                    return []
                stores = adapter._emart_stores(user_lat, user_lng)
                store_codes = ",".join(stores)
                def lookup(product):
                    code = str(DaisoMcpAdapter._first(product, "pluCd", "code"))
                    response = requests.get(
                        "https://everse.emart24.co.kr/api/stock/v3/stock-search/store",
                        params={"searchPluCode": code, "bizNoArr": store_codes},
                        headers={"x-requested-with": "XMLHttpRequest"},
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if (not isinstance(payload, dict) or
                            not isinstance(payload.get("storeGoodsInfo"), dict) or
                            not isinstance(payload.get("storeGoodsQty"), list)):
                        raise LiveDataError("이마트24 공식 재고 응답 형식이 올바르지 않습니다.")
                    normalized = {
                        "pluCd": code,
                        "inventory": {
                            "goodsInfo": payload["storeGoodsInfo"],
                            "stores": [{"bizNo": item.get("BIZNO"), "bizQty": item.get("BIZQTY")}
                                       for item in payload["storeGoodsQty"] if isinstance(item, dict)],
                        },
                        "_sourceMeta": {"date": response.headers.get("Date", ""),
                                        "age": response.headers.get("Age", "")},
                    }
                    rows = adapter._parse_emart_inventory(normalized, product, stores, user_lat, user_lng, now)
                    for row in rows:
                        row.stock_source = "이마트24 공식 재고 API (Track 2)"
                    return rows
                return DaisoMcpAdapter._collect_inventory(candidates, lookup, self.lookup_failures)

            region = self._region_keyword(user_lat, user_lng)
            rest = DaisoMcpAdapter("세븐일레븐")
            products = []
            for page in range(1, 11):
                found = rest._rest("/api/seveneleven/products", {"query": keyword, "size": 20, "page": page})
                batch = rest._find_lists(found, "products", "items")
                if not batch:
                    break
                products.extend(batch)
                total = DaisoMcpAdapter._parse_int((found.get("meta") or {}).get("total"), 0)
                if len(products) >= DaisoMcpAdapter.MAX_PRODUCTS or len(products) >= total:
                    if len(products) >= DaisoMcpAdapter.MAX_PRODUCTS and len(products) < total:
                        self.catalog_incomplete = True
                    break
            candidates = DaisoMcpAdapter._matching_products(
                products, keyword, ("itemName", "productName", "goodsName", "name"),
                ("itemCode", "code"),
            )
            def lookup(product):
                code = str(DaisoMcpAdapter._first(product, "itemCode", "code"))
                name = DaisoMcpAdapter._product_name(product)
                result = self.mcp.call("seveneleven_check_inventory", {
                    "keyword": name, "storeKeyword": region, "storeLimit": 20,
                })
                return [row for row in self._parse_seveneleven(result, name, now)
                        if row.product_code == code]
            return DaisoMcpAdapter._collect_inventory(candidates, lookup, self.lookup_failures)
        except (McpRequestError, requests.RequestException, OSError, ValueError) as exc:
            raise LiveDataError(f"{self.brand} {self.TRACK_NAME} 실패: {exc}") from exc

    def _search_cu(self, keyword: str, lat: float, lng: float, now: str) -> list[StoreProduct]:
        rest = DaisoMcpAdapter("CU")
        candidates = []
        seen = set()
        first_payload = None
        total = 0
        for offset in range(0, DaisoMcpAdapter.MAX_PRODUCTS, 20):
            payload = rest._rest("/api/cu/inventory", {
                "keyword": keyword, "lat": lat, "lng": lng,
                "size": 20, "offset": offset, "storeLimit": 20,
            })
            if offset == 0:
                first_payload = payload
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            inventory = data.get("inventory") or {}
            items = DaisoMcpAdapter._dict_list(inventory.get("items"))
            for index, product in enumerate(items):
                code = str(product.get("itemCode") or "")
                name = str(product.get("itemName") or "")
                if code and code not in seen and DaisoMcpAdapter._normalise(keyword) in DaisoMcpAdapter._normalise(name):
                    seen.add(code)
                    candidates.append((offset + index, product))
            total = DaisoMcpAdapter._parse_int(inventory.get("totalCount"), 0)
            if not items or offset + len(items) >= total or len(items) < 20:
                break
        if total > DaisoMcpAdapter.MAX_PRODUCTS:
            self.catalog_incomplete = True

        def lookup(candidate):
            offset, product = candidate
            payload = first_payload if offset == 0 else rest._rest("/api/cu/inventory", {
                "keyword": keyword, "lat": lat, "lng": lng,
                "size": 1, "offset": offset, "storeLimit": 20,
            })
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            if str((data.get("nearbyStores") or {}).get("stockItemCode") or "") != str(product["itemCode"]):
                return []
            return self._tag(DaisoMcpAdapter._parse_cu(data, keyword, now), self._source_timestamp(payload, now))

        return DaisoMcpAdapter._collect_inventory(candidates, lookup, self.lookup_failures)

    def _region_keyword(self, latitude: float, longitude: float) -> str:
        address = self.geocoder(latitude, longitude)
        parts = address.split()
        if not parts:
            raise LiveDataError("세븐일레븐 통합 조회에 필요한 위치 행정구역을 확인하지 못했습니다.")
        first = parts[0]
        for suffix in ("특별자치도", "특별자치시", "특별시", "광역시", "자치도", "도", "시"):
            if first.endswith(suffix) and len(first) > len(suffix):
                first = first[:-len(suffix)]
                break
        # A province/city plus district/county gives the store search a local
        # query while avoiding a broad nationwide lookup.
        return " ".join([first, *parts[1:2]])

    def _parse_seveneleven(self, result: dict[str, Any], keyword: str, now: str) -> list[StoreProduct]:
        product = result.get("product") if isinstance(result.get("product"), dict) else {}
        inventory = result.get("inventory") if isinstance(result.get("inventory"), dict) else {}
        name = str(product.get("itemName") or keyword)
        code = str(product.get("itemCode") or "")
        price = DaisoMcpAdapter._parse_int(product.get("salePrice") or product.get("price"), 0)
        promotion = str(product.get("eventName") or "")
        timestamp = self._source_timestamp(result, now)
        rows = []
        for store in DaisoMcpAdapter._dict_list(inventory.get("stores")):
            qty_raw = store.get("stockQuantity")
            qty, known = DaisoMcpAdapter._parse_stock(qty_raw, store)
            store_code = str(store.get("storeCode") or "")
            rows.append(StoreProduct(
                brand=self.brand,
                store_name=str(store.get("storeName") or ""),
                address=str(store.get("address") or ""),
                product_name=name,
                image_url=DaisoMcpAdapter._product_image(product),
                barcode=code if re.fullmatch(r"\d{8,14}", code) else "",
                product_code=code,
                price=price,
                promotion=promotion,
                stock=qty,
                stock_quantity_exact=DaisoMcpAdapter._stock_quantity_is_exact(qty_raw),
                latitude=DaisoMcpAdapter._parse_float(store.get("latitude")),
                longitude=DaisoMcpAdapter._parse_float(store.get("longitude")),
                distance_km=DaisoMcpAdapter._parse_distance(store.get("distanceM")),
                data_source="LIVE",
                updated_at=now,
                store_code=store_code,
                stock_source=f"{self.TRACK_NAME} (Track 2)",
                price_source=f"{self.TRACK_NAME} (Track 2)" if price else "미제공",
                promotion_source=f"{self.TRACK_NAME} (Track 2)" if promotion else "미제공",
                stock_updated_at=timestamp,
                price_updated_at=timestamp if price else "",
                stock_known=known,
                promotion_known=bool(promotion),
                price_data_status="LIVE" if price else "UNKNOWN",
                stock_data_status="LIVE" if known else "UNKNOWN",
                promotion_data_status="LIVE" if promotion else "UNKNOWN",
            ))
        return rows

    def _parse_gs25(self, result: dict[str, Any], keyword: str, now: str,
                    selected_product: dict[str, Any] | None = None) -> list[StoreProduct]:
        product = result.get("product") if isinstance(result.get("product"), dict) else {}
        inventory = result.get("inventory") if isinstance(result.get("inventory"), dict) else {}
        selected_product = selected_product or {}
        response_name = str(product.get("name") or product.get("productName") or "")
        selected_name = DaisoMcpAdapter._product_name(selected_product)
        name = (response_name if response_name and
                DaisoMcpAdapter._normalise(response_name) != DaisoMcpAdapter._normalise(keyword)
                else selected_name or response_name or keyword)
        code = str(result.get("itemCode") or result.get("itemCodeUsed") or product.get("itemCode") or
                   DaisoMcpAdapter._first(selected_product, "itemCode", "code") or "")
        price = DaisoMcpAdapter._parse_int(
            product.get("sellPrice") or product.get("price") or product.get("searchItemSellPrice"), 0
        )
        timestamp = self._source_timestamp(result, now)
        rows = []
        for store in DaisoMcpAdapter._dict_list(inventory.get("stores")):
            qty_raw = store.get("realStockQuantity")
            if qty_raw is None:
                qty_raw = store.get("stockQuantity")
            qty, known = DaisoMcpAdapter._parse_stock(qty_raw, store)
            store_code = str(store.get("storeCode") or "")
            rows.append(StoreProduct(
                brand=self.brand,
                store_name=str(store.get("storeName") or ""),
                address=str(store.get("address") or ""),
                product_name=str(store.get("searchItemName") or name),
                image_url=DaisoMcpAdapter._product_image(product),
                barcode=code if re.fullmatch(r"\d{8,14}", code) else "",
                product_code=code,
                price=DaisoMcpAdapter._parse_int(store.get("searchItemSellPrice"), price),
                promotion=str(store.get("eventName") or ""),
                stock=qty,
                stock_quantity_exact=DaisoMcpAdapter._stock_quantity_is_exact(qty_raw),
                latitude=DaisoMcpAdapter._parse_float(store.get("latitude")),
                longitude=DaisoMcpAdapter._parse_float(store.get("longitude")),
                distance_km=DaisoMcpAdapter._parse_distance(store.get("distanceM")),
                data_source="LIVE",
                updated_at=now,
                store_code=store_code,
                stock_source=f"{self.TRACK_NAME} (Track 2)",
                price_source=f"{self.TRACK_NAME} (Track 2)" if price else "미제공",
                promotion_source=f"{self.TRACK_NAME} (Track 2)" if store.get("eventName") else "미제공",
                stock_updated_at=timestamp,
                price_updated_at=timestamp if price else "",
                stock_known=known,
                promotion_known=bool(store.get("eventName")),
                price_data_status="LIVE" if price else "UNKNOWN",
                stock_data_status="LIVE" if known else "UNKNOWN",
                promotion_data_status="LIVE" if store.get("eventName") else "UNKNOWN",
            ))
        return rows

    @staticmethod
    def _tag(rows: list[StoreProduct], now: str) -> list[StoreProduct]:
        for row in rows:
            row.stock_source = "Daiso MCP REST 통합조회 (Track 2)"
            row.stock_updated_at = now
        return rows

    @staticmethod
    def _source_timestamp(result: dict[str, Any], fallback: str) -> str:
        for key in ("stockUpdatedAt", "updatedAt", "retrievedAt", "fetchedAt", "cachedAt", "timestamp"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        for key in ("stockUpdatedAt", "updatedAt", "retrievedAt", "fetchedAt", "cachedAt", "timestamp"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        source_meta = result.get("_sourceMeta") if isinstance(result.get("_sourceMeta"), dict) else {}
        date_header = source_meta.get("date")
        if isinstance(date_header, str) and date_header.strip():
            try:
                observed = parsedate_to_datetime(date_header)
                age = float(source_meta.get("age") or 0)
                observed = observed.replace(tzinfo=observed.tzinfo or timezone.utc)
                observed = observed.timestamp() - max(age, 0)
                return datetime.fromtimestamp(observed, tz=timezone.utc).isoformat(timespec="seconds")
            except (TypeError, ValueError, OverflowError):
                pass
        return fallback


def merge_inventory_tracks(track_results: list[tuple[str, list[StoreProduct]]]) -> list[StoreProduct]:
    """Keep unique product/store rows and choose the newest observation per pair."""
    chosen: list[tuple[str, StoreProduct]] = []
    for track_name, rows in track_results:
        for row in rows:
            existing_index = next((index for index, (_, previous) in enumerate(chosen)
                                   if same_product(previous, row) and same_store(previous, row)), None)
            if existing_index is None:
                chosen.append((track_name, row))
                continue
            previous_track, previous = chosen[existing_index]
            if _freshness_key(row, track_name) > _freshness_key(previous, previous_track):
                chosen[existing_index] = (track_name, row)
    return [row for _, row in chosen]


def _freshness_key(row: StoreProduct, track_name: str) -> tuple[datetime, int, int]:
    raw_timestamp = row.stock_updated_at or row.updated_at
    try:
        observed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        observed = datetime.min.replace(tzinfo=timezone.utc)
    # At equal timestamps prefer a confirmed stock value, then Track 2.
    return observed, int(row.stock_known), int("Track 2" in track_name)
