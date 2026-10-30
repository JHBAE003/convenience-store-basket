from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
import re
import time
from typing import Any

import requests

from core.config import REQUEST_TIMEOUT_SECONDS
from core.data.base_adapter import DataAdapter
from core.data.mcp_client import McpClient, McpRequestError
from core.models import StoreProduct
from core.services.distance import haversine_km
from core.services.geocoding import emart24_region_korean


class LiveDataError(RuntimeError):
    """A remote request failed or returned data that cannot safely be interpreted."""


class DaisoMcpAdapter(DataAdapter):
    """Read-only adapter for the currently documented mcp.aka.page services.

    CU and Emart24 use documented MCP tools. GS25 and SevenEleven use the
    service's documented REST routes because their MCP tool counterparts are
    not present in the current service-reference tool list.
    """

    REST = "https://mcp.aka.page"
    MAX_PRODUCTS = 100
    PRODUCT_WORKERS = 3

    def __init__(self, brand: str, session: requests.Session | None = None,
                 mcp_client: McpClient | None = None,
                 emart_region: tuple[str, str] | None = None):
        if brand not in {"GS25", "CU", "세븐일레븐", "이마트24"}:
            raise ValueError(f"지원하지 않는 브랜드: {brand}")
        self.brand = brand
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ConvenienceStoreCompare/1.0 (read-only)", "Accept": "application/json"})
        self.mcp = mcp_client or McpClient(timeout=REQUEST_TIMEOUT_SECONDS, session=self.session)
        self.lookup_failures: list[str] = []
        self.catalog_incomplete = False
        self.emart_region = emart_region

    def search(self, keyword: str, user_lat: float, user_lng: float) -> list[StoreProduct]:
        query = keyword.strip()
        if not query:
            raise LiveDataError("실시간 조회에는 상품 검색어가 필요합니다.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            if self.brand == "CU":
                return self._search_cu(query, user_lat, user_lng, now)
            if self.brand == "이마트24":
                return self._search_emart(query, user_lat, user_lng, now)
            if self.brand == "GS25":
                return self._search_gs25(query, user_lat, user_lng, now)
            return self._search_seven(query, user_lat, user_lng, now)
        except (McpRequestError, requests.RequestException) as exc:
            raise LiveDataError(str(exc)) from exc

    def _search_cu(self, query: str, lat: float, lng: float, now: str) -> list[StoreProduct]:
        candidates: list[tuple[int, dict[str, Any]]] = []
        seen = set()
        first_payload: dict[str, Any] | None = None
        total = 0
        for offset in range(0, self.MAX_PRODUCTS, 20):
            payload = self.mcp.call("cu_check_inventory", {
                "keyword": query, "latitude": lat, "longitude": lng,
                "size": 20, "offset": offset, "storeLimit": 20,
            })
            if offset == 0:
                first_payload = payload
            inventory = payload.get("inventory") or {}
            items = self._dict_list(inventory.get("items"))
            for index, item in enumerate(items):
                code = str(item.get("itemCode") or "")
                name = str(item.get("itemName") or "")
                if code and code not in seen and self._normalise(query) in self._normalise(name):
                    seen.add(code)
                    candidates.append((offset + index, item))
            total = self._parse_int(inventory.get("totalCount"), 0)
            if not items or offset + len(items) >= total or len(items) < 20:
                break
        if total > self.MAX_PRODUCTS:
            self.catalog_incomplete = True

        def lookup(candidate: tuple[int, dict[str, Any]]) -> list[StoreProduct]:
            offset, product = candidate
            payload = first_payload if offset == 0 else self.mcp.call("cu_check_inventory", {
                "keyword": query, "latitude": lat, "longitude": lng,
                "size": 1, "offset": offset, "storeLimit": 20,
            })
            if str((payload.get("nearbyStores") or {}).get("stockItemCode") or "") != str(product["itemCode"]):
                return []
            return self._parse_cu(payload, query, now)

        return self._collect_inventory(candidates, lookup, self.lookup_failures)

    def _search_emart(self, query: str, lat: float, lng: float, now: str) -> list[StoreProduct]:
        candidates = self._emart_products(query)
        if not candidates:
            return []
        stores = self._emart_stores(lat, lng)
        codes = list(stores)
        def lookup(product: dict[str, Any]) -> list[StoreProduct]:
            code = str(self._first(product, "pluCd", "code"))
            inventory = self.mcp.call("emart24_check_inventory", {"pluCd": code, "bizNoArr": ",".join(codes)})
            return self._parse_emart_inventory(inventory, product, stores, lat, lng, now)

        # Probe one product before launching parallel lookups. The MCP service can
        # return unrelated stores even when bizNoArr contains only local stores.
        first_rows = lookup(candidates[0])
        if len(candidates) == 1:
            return first_rows
        try:
            remaining_rows = self._collect_inventory(candidates[1:], lookup, self.lookup_failures)
        except LiveDataError:
            if not first_rows:
                raise
            self.catalog_incomplete = True
            remaining_rows = []
        return first_rows + remaining_rows

    def _emart_products(self, query: str) -> list[dict[str, Any]]:
        products = []
        expected_total = 0
        for page in range(1, 11):
            found = self.mcp.call("emart24_search_products", {"keyword": query, "page": page, "pageSize": 10})
            batch = self._dict_list(found.get("products"))
            expected_total = max(expected_total, self._parse_int(found.get("totalCount"), 0))
            if not batch:
                if products and len(products) < expected_total:
                    self.catalog_incomplete = True
                break
            products.extend(batch)
            if (len(products) >= self.MAX_PRODUCTS or
                    (expected_total and len(products) >= expected_total) or len(batch) < 10):
                if len(products) >= self.MAX_PRODUCTS and len(products) < expected_total:
                    self.catalog_incomplete = True
                break
        return self._matching_products(products, query, ("goodsName", "goodsNm", "name"), ("pluCd", "code"))

    def _emart_stores(self, lat: float, lng: float) -> dict[str, dict[str, Any]]:
        area1, area2 = self.emart_region or emart24_region_korean(lat, lng)
        arguments = {"area1": area1, "latitude": lat, "longitude": lng, "limit": 50}
        if area2:
            arguments["area2"] = area2
        result = self.mcp.call("emart24_find_nearby_stores", arguments)
        selected: dict[str, dict[str, Any]] = {}
        for store in self._find_lists(result, "stores"):
            code = str(self._first(store, "bizNo", "storeCode", "code", "CODE", "BIZNO") or "").strip()
            address = str(self._first(store, "address", "ADDRESS", "ADDRESS_DE") or "")
            if area2 and address and area2 not in address:
                continue
            if code:
                selected[code] = store
        if not selected:
            raise LiveDataError(f"이마트24 {area1} {area2} 점포를 찾지 못했거나 점포 코드가 없습니다.")
        return selected

    def _parse_emart_inventory(self, response: dict[str, Any], product: dict[str, Any],
                               selected_stores: dict[str, dict[str, Any]],
                               lat: float, lng: float, now: str) -> list[StoreProduct]:
        payload = response.get("data") if isinstance(response.get("data"), dict) else response
        inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else payload
        if inventory.get("available") is False:
            raise LiveDataError(str(inventory.get("unavailableReason") or "이마트24 재고 조회 불가"))
        goods = inventory.get("goodsInfo") if isinstance(inventory.get("goodsInfo"), dict) else {}
        code = str(self._first(product, "pluCd", "code") or "")
        reported_code = str(self._first(payload, "pluCd", default=self._first(goods, "pluCd", "PLU_CD")) or "")
        if reported_code and reported_code != code:
            raise LiveDataError(f"이마트24 재고 상품 코드가 요청 코드와 다릅니다: {code}")
        name = str(self._first(product, "goodsName", "goodsNm", "name", default=self._first(goods, "goodsName", "goodsNm")) or "")
        price = self._parse_int(self._first(product, "viewPrice", "salePrice", "price",
                                            default=self._first(goods, "viewPrice", "salePrice")), 0)
        promotion = str(self._first(goods, "eventName", default="") or "")
        timestamp = self._source_timestamp(response, now)
        stock_stores = self._dict_list(inventory.get("stores"))
        if stock_stores and not any(
            str(self._first(store, "bizNo", "storeCode", "BIZNO") or "").strip() in selected_stores
            for store in stock_stores
        ):
            raise LiveDataError("이마트24 MCP 재고 응답이 요청한 지역 점포와 일치하지 않습니다.")
        rows = []
        for stock_store in stock_stores:
            store_code = str(self._first(stock_store, "bizNo", "storeCode", "BIZNO") or "").strip()
            store = selected_stores.get(store_code)
            if not store:
                continue
            qty_raw = self._first(stock_store, "bizQty", "BIZQTY", "stockQuantity")
            qty, known = self._parse_stock(qty_raw, stock_store)
            store_lat = self._parse_float(self._first(store, "latitude", "LATITUDE", "lat"))
            store_lng = self._parse_float(self._first(store, "longitude", "LONGITUDE", "lng"))
            distance = haversine_km(lat, lng, store_lat, store_lng)
            if distance is None:
                distance = self._parse_distance(self._first(store, "distanceM", "distanceKm"))
            rows.append(StoreProduct(
                brand="이마트24",
                store_name=str(self._first(store, "storeName", "name", "TITLE", default=self._first(stock_store, "storeName")) or ""),
                address=str(self._first(store, "address", "ADDRESS", "ADDRESS_DE", default=self._first(stock_store, "address")) or ""),
                product_name=name, barcode=code if re.fullmatch(r"\d{8,14}", code) else "",
                product_code=code, price=price, promotion=promotion, stock=qty,
                image_url=self._product_image(product) or self._product_image(goods),
                stock_quantity_exact=self._stock_quantity_is_exact(qty_raw),
                latitude=store_lat, longitude=store_lng, distance_km=distance,
                data_source="LIVE", updated_at=now, store_code=store_code,
                stock_source="Daiso MCP", price_source="Daiso MCP" if price else "미제공",
                promotion_source="Daiso MCP" if promotion else "미제공",
                stock_updated_at=timestamp, price_updated_at=timestamp if price else "",
                stock_known=known, promotion_known=bool(promotion),
                price_data_status="LIVE" if price else "UNKNOWN",
                stock_data_status="LIVE" if known else "UNKNOWN",
                promotion_data_status="LIVE" if promotion else "UNKNOWN",
            ))
        return rows

    def _search_gs25(self, query: str, lat: float, lng: float, now: str) -> list[StoreProduct]:
        products = self._rest("/api/gs25/products", {"keyword": query, "limit": self.MAX_PRODUCTS, "lat": lat, "lng": lng})
        product_list = self._find_lists(products, "products", "items")
        candidates = self._matching_products(product_list, query,
                                             ("productName", "goodsName", "itemName", "name"),
                                             ("itemCode", "code", "goodsCode"))

        def lookup(product: dict[str, Any]) -> list[StoreProduct]:
            code = str(self._first(product, "itemCode", "code", "goodsCode"))
            inventory = self._rest("/api/gs25/inventory", {
                "itemCode": code, "lat": lat, "lng": lng, "storeLimit": 20,
            })
            return [row for row in self._extract_inventory_records(inventory, query, now, product)
                    if row.product_code == code]

        return self._collect_inventory(candidates, lookup, self.lookup_failures)

    def _search_seven(self, query: str, lat: float, lng: float, now: str) -> list[StoreProduct]:
        product_list = []
        for page in range(1, 11):
            found = self._rest("/api/seveneleven/products", {"query": query, "size": 20, "page": page})
            batch = self._find_lists(found, "products", "items")
            if not batch:
                break
            product_list.extend(batch)
            total = self._parse_int((found.get("meta") or {}).get("total"), 0)
            if len(product_list) >= self.MAX_PRODUCTS or len(product_list) >= total:
                if len(product_list) >= self.MAX_PRODUCTS and len(product_list) < total:
                    self.catalog_incomplete = True
                break
        candidates = self._matching_products(product_list, query,
                                             ("itemName", "productName", "goodsName", "name"),
                                             ("itemCode", "code"))

        def lookup(product: dict[str, Any]) -> list[StoreProduct]:
            selected_name = self._product_name(product)
            code = str(self._first(product, "itemCode", "code"))
            inventory = self._rest("/api/seveneleven/inventory", {
                "keyword": selected_name, "lat": lat, "lng": lng, "storeLimit": 20,
            })
            return [row for row in self._extract_inventory_records(inventory, selected_name, now, product)
                    if row.product_code == code]

        return self._collect_inventory(candidates, lookup, self.lookup_failures)

    @classmethod
    def _matching_products(cls, products: list[dict[str, Any]], query: str,
                           name_keys: tuple[str, ...], code_keys: tuple[str, ...]) -> list[dict[str, Any]]:
        needle = cls._normalise(query)
        seen = set()
        result = []
        for product in products:
            code = str(cls._first(product, *code_keys) or "")
            name = str(cls._first(product, *name_keys) or "")
            if code and code not in seen and needle and needle in cls._normalise(name):
                seen.add(code)
                result.append(product)
            if len(result) >= cls.MAX_PRODUCTS:
                break
        return result

    @classmethod
    def _collect_inventory(cls, candidates: list[Any], lookup,
                           failures: list[str] | None = None) -> list[StoreProduct]:
        if not candidates:
            return []
        records: list[StoreProduct] = []
        errors = []
        with ThreadPoolExecutor(max_workers=min(cls.PRODUCT_WORKERS, len(candidates))) as pool:
            futures = [pool.submit(lookup, candidate) for candidate in candidates]
            for candidate, future in zip(candidates, futures):
                try:
                    records.extend(future.result())
                except Exception as exc:
                    errors.append(str(exc))
                    if failures is not None:
                        product = candidate[1] if isinstance(candidate, tuple) else candidate
                        failures.append(str(cls._first(product, "itemCode", "pluCd", "code") or ""))
        if not records and errors:
            raise LiveDataError(f"상품별 재고 조회 실패: {errors[0]}")
        return records

    def _rest(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = None
        for attempt in range(2):
            try:
                response = self.session.get(self.REST + path, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
                if response.status_code >= 500 and attempt == 0:
                    time.sleep(0.35)
                    continue
                break
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 0:
                    time.sleep(0.35)
                    continue
                raise LiveDataError(f"{self.brand} 네트워크 연결 실패: {exc}") from exc
        if response is None:
            raise LiveDataError(f"{self.brand} REST 요청 실패: 응답이 없습니다.")
        if response.status_code == 429:
            raise LiveDataError(f"{self.brand} API 호출 제한(429)")
        if response.status_code >= 500:
            raise LiveDataError(f"{self.brand} 공개 API 서버 오류(HTTP {response.status_code})")
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LiveDataError(f"{self.brand} API HTTP/JSON 응답 오류: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("success") is False or payload.get("error"):
            raise LiveDataError(f"{self.brand} API가 오류 응답을 반환했습니다: {payload}")
        payload["_sourceMeta"] = {
            "date": response.headers.get("Date", ""),
            "age": response.headers.get("Age", ""),
            "cacheStatus": response.headers.get("CF-Cache-Status", "") or response.headers.get("X-Cache", ""),
        }
        return payload

    @staticmethod
    def _walk(node: Any):
        if isinstance(node, dict):
            yield node
            for child in node.values():
                yield from DaisoMcpAdapter._walk(child)
        elif isinstance(node, list):
            for child in node:
                yield from DaisoMcpAdapter._walk(child)

    @classmethod
    def _find_lists(cls, payload: Any, *names: str) -> list[dict[str, Any]]:
        for node in cls._walk(payload):
            for name in names:
                if isinstance(node.get(name), list):
                    return cls._dict_list(node[name])
        return []

    @staticmethod
    def _dict_list(value: Any) -> list[dict[str, Any]]:
        return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []

    @staticmethod
    def _first(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        return default

    @staticmethod
    def _normalise(value: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]", "", (value or "").lower())

    @classmethod
    def _best_product(cls, products: list[dict[str, Any]], query: str,
                      name_keys: tuple[str, ...], code_keys: tuple[str, ...]) -> dict[str, Any]:
        needle = cls._normalise(query)
        eligible = []
        for item in products:
            name = str(cls._first(item, *name_keys) or "")
            code = cls._first(item, *code_keys)
            normalized = cls._normalise(name)
            if code and needle and (needle == normalized or needle in normalized):
                eligible.append((0 if needle == normalized else 1, len(normalized), item))
        if not eligible:
            raise LiveDataError("상품 검색 결과에서 검색어와 일치하는 상품 코드가 없습니다.")
        eligible.sort(key=lambda item: (item[0], item[1]))
        return eligible[0][2]

    @classmethod
    def _product_name(cls, data: dict[str, Any]) -> str:
        return str(cls._first(data, "productName", "goodsName", "itemName", "name", "title", default="") or "").strip()

    @staticmethod
    def _product_image(data: dict[str, Any]) -> str:
        for key in ('image_url', 'imageUrl', 'productImageUrl', 'itemImageUrl', 'goodsImageUrl',
                    'imgUrl', 'image', 'thumbnailUrl', 'imagePath', 'goodsImgUrl'):
            value = data.get(key)
            if isinstance(value, str) and value.startswith(('https://', 'http://', '//')):
                return 'https:' + value if value.startswith('//') else value
        return ''

    @classmethod
    def _parse_cu(cls, payload: dict[str, Any], query: str, now: str) -> list[StoreProduct]:
        inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
        nearby = payload.get("nearbyStores") if isinstance(payload.get("nearbyStores"), dict) else {}
        if inventory.get("available") is False or nearby.get("available") is False:
            reason = inventory.get("unavailableReason") or nearby.get("unavailableReason") or "서비스에서 조회 불가로 반환했습니다."
            raise LiveDataError(f"CU 조회 불가: {reason}")
        items = cls._dict_list(inventory.get("items"))
        chosen = next((item for item in items if str(item.get("itemCode", "")) == str(nearby.get("stockItemCode", ""))), None)
        if not chosen:
            # MCP explicitly reports which first candidate supplied nearby stock;
            # never join a different inventory candidate to those stores.
            raise LiveDataError("CU 재고 대상 상품 코드와 상품 목록의 코드가 일치하지 않습니다.")
        name = str(chosen.get("itemName") or query)
        code = str(chosen.get("itemCode") or "")
        price = cls._parse_int(chosen.get("price"), 0)
        records = []
        for store in cls._dict_list(nearby.get("stores")):
            stock = store.get("stock")
            qty, known = cls._parse_stock(stock, store)
            records.append(StoreProduct(
                brand="CU", store_name=str(store.get("storeName") or ""),
                address=str(store.get("address") or ""), product_name=name,
                barcode=code if re.fullmatch(r"\d{8,14}", code) else "", product_code=code,
                price=price, promotion="", stock=qty, stock_quantity_exact=known,
                image_url=cls._product_image(chosen),
                latitude=cls._parse_float(store.get("latitude")), longitude=cls._parse_float(store.get("longitude")),
                distance_km=cls._parse_distance(store.get("distanceM")), data_source="LIVE", updated_at=now,
                store_code=str(store.get("storeCode") or ""), stock_known=known,
                stock_source="Daiso MCP", price_source="Daiso MCP", promotion_source="미제공",
                promotion_known=False,
            ))
        return records

    def _extract_inventory_records(self, payload: Any, keyword: str, now: str,
                                   product: dict[str, Any] | None = None) -> list[StoreProduct]:
        found: list[StoreProduct] = []
        source_timestamp = self._source_timestamp(payload, now)
        fallback_product_name = self._product_name(product or {}) or keyword
        fallback_price = self._parse_int(self._first(product or {}, "price", "salePrice", "sellPrice",
                                                    "sellingPrice", "normalPrice", "viewPrice"), 0)
        fallback_code = str(self._first(product or {}, "itemCode", "code", "goodsCode", "pluCd") or "")
        for node in self._walk(payload):
            nested = node.get("store") or node.get("storeInfo") or node.get("storeInventory")
            merged = {**nested, **node} if isinstance(nested, dict) else node
            name = self._first(merged, "storeName", "store_nm", "store_name", "shopName", "storeNm", "branchName")
            address = self._first(merged, "address", "addr", "storeAddress", "roadAddress", "storeAddr")
            store_code = self._first(merged, "storeCode", "storeId", "storeCd", "bizNo", default="")
            if not name and not address and not store_code:
                continue
            stock_raw = self._first(merged, "quantity", "realStockQuantity", "bizQty", "stockQty", "remainQty", "remainQuantity", "qty",
                                    "stockQuantity", "remainCnt", "stockCnt", "stock", "stockLabel", "stockStatus")
            if stock_raw is None:
                continue
            stock, known = self._parse_stock(stock_raw, merged)
            product_name = str(self._first(merged, "productName", "goodsName", "itemName", "name", default=fallback_product_name) or fallback_product_name)
            code = str(self._first(merged, "itemCode", "code", "goodsCode", "pluCd", "barcode", default=fallback_code) or fallback_code)
            price_raw = self._first(merged, "price", "salePrice", "sellPrice", "searchItemSellPrice",
                                    "sellingPrice", "priceToPay", "finalPrice", "viewPrice")
            promo = str(self._first(merged, "promotion", "event", "eventName", "promotionName", "eventText", default="") or "")
            found.append(StoreProduct(
                brand=self.brand, store_name=str(name or ""), address=str(address or ""),
                product_name=product_name,
                barcode=str(self._first(merged, "barcode", "ean", "gtin", "upc", default="") or ""),
                product_code=code, price=self._parse_int(price_raw, fallback_price),
                image_url=self._product_image(product or {}) or self._product_image(merged),
                promotion=promo, stock=stock, stock_quantity_exact=self._stock_quantity_is_exact(stock_raw),
                latitude=self._parse_float(self._first(merged, "latitude", "lat", "storeLat")),
                longitude=self._parse_float(self._first(merged, "longitude", "lng", "lon", "storeLng")),
                distance_km=self._parse_distance(self._first(merged, "distanceKm", "distanceM", "distance", "distance_km")),
                data_source="LIVE", updated_at=now,
                store_code=str(store_code or ""),
                stock_updated_at=source_timestamp,
                price_updated_at=source_timestamp if fallback_price or price_raw else "",
                stock_known=known, stock_source="Daiso MCP", price_source="Daiso MCP",
                promotion_source="Daiso MCP" if promo else "미제공", promotion_known=bool(promo),
            ))
        # Deduplicate only when a real store identifier exists; same-named branches
        # are not assumed to be the same store.
        result: dict[tuple[str, str, str], StoreProduct] = {}
        for row in found:
            key = (row.store_code, row.product_name, row.store_name) if row.store_code else (row.address, row.product_name, row.store_name)
            result[key] = row
        return list(result.values())

    @staticmethod
    def _source_timestamp(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ("stockUpdatedAt", "updatedAt", "retrievedAt", "fetchedAt", "cachedAt", "timestamp"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            for key in ("stockUpdatedAt", "updatedAt", "retrievedAt", "fetchedAt", "cachedAt", "timestamp"):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            source_meta = payload.get("_sourceMeta") if isinstance(payload.get("_sourceMeta"), dict) else {}
            date_header = source_meta.get("date")
            if isinstance(date_header, str) and date_header.strip():
                try:
                    observed = parsedate_to_datetime(date_header)
                    age = float(source_meta.get("age") or 0)
                    observed = observed.replace(tzinfo=observed.tzinfo or timezone.utc)
                    return datetime.fromtimestamp(observed.timestamp() - max(age, 0), tz=timezone.utc).isoformat(timespec="seconds")
                except (TypeError, ValueError, OverflowError):
                    pass
        return fallback

    @staticmethod
    def _parse_stock(value: Any, data: dict[str, Any]) -> tuple[int, bool]:
        if isinstance(value, bool):
            return int(value), True
        if isinstance(value, (int, float)):
            return max(int(value), 0), True
        text = str(value or "").strip().lower().replace("_", " ")
        if not text or text in {"null", "none", "unknown", "미확인"}:
            return 0, False
        number = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", text.replace(",", ""))
        if number:
            return max(int(float(number.group(1))), 0), True
        number = re.search(r"(\d[\d,]*)\s*개?", text)
        if number and any(word in text for word in ("재고", "stock", "remain", "수량")):
            return int(number.group(1).replace(",", "")), True
        if text in {"false", "n", "no", "out of stock", "sold out", "0개"} or any(x in text for x in ("품절", "없음", "sold out")):
            return 0, True
        if text in {"true", "y", "yes", "in stock", "available"} or any(x in text for x in ("재고 있음", "보유", "가능", "in stock")):
            return 1, True
        return 0, False

    @staticmethod
    def _stock_quantity_is_exact(value: Any) -> bool:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        return bool(re.fullmatch(r"\s*\d+(?:\.0+)?\s*", str(value or "")))

    @staticmethod
    def _parse_int(value: Any, default: int = 0) -> int:
        if value in (None, ""):
            return default
        try:
            return int(float(re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_distance(cls, value: Any) -> float | None:
        if value in (None, ""):
            return None
        text = str(value).strip().lower().replace(",", "")
        try:
            number = float(re.sub(r"[^0-9.\-]", "", text))
        except ValueError:
            return None
        return number / 1000 if "m" in text and "km" not in text or number >= 100 else number
