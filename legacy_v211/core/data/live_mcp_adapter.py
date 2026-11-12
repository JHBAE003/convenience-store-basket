from __future__ import annotations

from datetime import datetime
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

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class DaisoMcpAdapter(DataAdapter):
    """Read-only adapter for the currently documented mcp.aka.page services.

    New product/selected-inventory calls use the documented unified MCP tools.
    Legacy broad searches remain available for compatibility; REST is only a
    conditional recovery path and is not an independent source of evidence.
    """

    REST = "https://mcp.aka.page"
    MAX_PRODUCTS = 100
    CANDIDATE_LIMIT = 20
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
        self.request_count = 0

    SERVICE = {"GS25": "gs25", "CU": "cu", "세븐일레븐": "seveneleven", "이마트24": "emart24"}

    @staticmethod
    def safe_payload(value: Any) -> Any:
        """Keep original public data/errors without credentials or session fields."""
        if isinstance(value, dict):
            return {key: DaisoMcpAdapter.safe_payload(item) for key, item in value.items()
                    if not any(secret in re.sub(r"[^a-z0-9]", "", str(key).casefold())
                               for secret in ("authorization", "cookie", "token", "password", "apikey", "secret",
                                              "credential", "sessionid", "sessionkey", "signenc", "csrf", "xsrf"))}
        if isinstance(value, list):
            return [DaisoMcpAdapter.safe_payload(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"(?i)\b(bearer|basic)\s+[^\s,;\"']+", r"\1 [삭제]", value)
            value = re.sub(r"(?i)(https?://)[^/\s?#@]+@", r"\1[삭제]@", value)
            value = re.sub(r"(?im)((?:set-)?cookie\s*:\s*)[^\r\n]+", r"\1[삭제]", value)
            value = re.sub(r"(?i)((?:api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|password|secret|sign[_-]?enc|session[_-]?(?:id|key)|csrf|xsrf)\s*[=:]\s*)[^\s&\"',;]+",
                           r"\1[삭제]", value)
        return value

    def _unified(self, operation: str, **arguments) -> dict[str, Any]:
        self.request_count += 1
        payload = self.mcp.call(operation, {"service": self.SERVICE[self.brand], **arguments})
        if not isinstance(payload, dict) or payload.get("error") or payload.get("success") is False:
            raise LiveDataError("조회 응답이 오류이거나 읽을 수 없는 형식입니다.")
        return payload

    def search_products(self, keyword: str, latitude: float, longitude: float) -> list[StoreProduct]:
        """Candidate discovery only; catalog prices never become store prices."""
        query = keyword.strip()
        if not query:
            raise LiveDataError("상품명을 입력해 주세요.")
        if self.brand == "CU":
            # CU has no separate search_products service in the public schema.
            payload = self._unified("check_inventory", keyword=query, latitude=latitude,
                                    longitude=longitude, size=20, offset=0, storeLimit=1)
            inventory = payload.get("inventory")
            if not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list):
                raise LiveDataError("상품 후보 목록을 읽지 못했습니다.")
            if inventory.get("available") is False:
                raise LiveDataError(str(inventory.get("unavailableReason") or "상품 검색을 이용할 수 없습니다."))
            products = inventory["items"]
            self.catalog_incomplete = self._parse_int(inventory.get("totalCount"), 0) > len(products)
        else:
            arguments = {"query": query, "size": 20, "page": 1} if self.brand == "세븐일레븐" else {"keyword": query}
            if self.brand == "GS25":
                arguments["limit"] = 20
            if self.brand == "이마트24":
                arguments.update(page=1, pageSize=20)
            payload = self._unified("search_products", **arguments)
            standard = payload.get("standard") or {}
            products = standard.get("products")
            if not isinstance(products, list):
                products = self._find_lists(payload, "products", "items")
                if not products and not any(isinstance(node.get(key), list)
                                            for node in self._walk(payload) for key in ("products", "items")):
                    raise LiveDataError("상품 후보 목록을 읽지 못했습니다.")
            self.catalog_incomplete = self._parse_int(payload.get("totalCount"), 0) > len(products)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        source_time = self._source_timestamp(payload, "")
        # Enforce our requested candidate page even if an upstream ignores it.
        # Preserve its complete sanitized response once rather than copying it
        # into every result and amplifying an oversized response quadratically.
        self.catalog_incomplete = self.catalog_incomplete or len(products) > self.CANDIDATE_LIMIT
        products = products[:self.CANDIDATE_LIMIT]
        public_response = self.safe_payload(payload)
        rows = []
        seen = set()
        for product in products:
            if not isinstance(product, dict):
                continue
            raw = product.get("raw") if isinstance(product.get("raw"), dict) else product
            code = str(self._first(product, "code", default=self._first(raw, "itemCode", "pluCd", "goodsCode", "code")) or "")
            name = str(self._first(product, "name", default=self._product_name(raw)) or "")
            if not code or not name or code in seen:
                continue
            seen.add(code)
            price = self._parse_int(self._first(product, "price", default=self._first(raw, "price", "viewPrice", "salePrice")), 0)
            rows.append(StoreProduct(
                self.brand, "", "", name, product_code=code,
                barcode=str(self._first(raw, "barcode", "ean", "gtin", "upc", default="") or ""),
                price=price, image_url=self._product_image(product) or self._product_image(raw),
                stock_known=False, promotion_known=False, data_source="LIVE", updated_at=now,
                received_at=now, source_updated_at=source_time,
                price_source="상품 목록 참고 가격" if price else "미제공",
                price_data_status="REFERENCE" if price else "UNKNOWN",
                raw_payload={"product":self.safe_payload(raw), "response":public_response},
            ))
        return rows

    def find_stores(self, latitude: float, longitude: float) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {"latitude": latitude, "longitude": longitude, "limit": 20}
        if self.brand == "이마트24":
            region = self.emart_region or emart24_region_korean(latitude, longitude)
            arguments.update(area1=region[0], area2=region[1])
        if self.brand == "세븐일레븐":
            arguments = {"keyword": self._region_keyword(latitude, longitude), "limit": 20}
        payload = self._unified("find_stores", **arguments)
        standard = payload.get("standard") or {}
        stores = standard.get("stores")
        if not isinstance(stores, list):
            stores = self._find_lists(payload, "stores", "items")
            if not stores and not any(isinstance(node.get("stores"), list) for node in self._walk(payload)):
                raise LiveDataError("매장 목록을 읽지 못했습니다.")
        return self.safe_payload(stores)

    def _region_keyword(self, latitude: float, longitude: float) -> str:
        area1, area2 = self.emart_region or emart24_region_korean(latitude, longitude)
        for suffix in ("특별자치도", "특별자치시", "특별시", "광역시", "도"):
            if area1.endswith(suffix):
                area1 = area1[:-len(suffix)]
                break
        return " ".join(part for part in (area1, area2) if part)

    def check_selected(self, link: Any, latitude: float, longitude: float) -> list[StoreProduct]:
        code, name = str(link.product_code), str(link.product_name)
        if not code:
            raise LiveDataError("선택 상품 코드가 없습니다. 상품을 다시 선택해 주세요.")
        arguments: dict[str, Any] = {"latitude": latitude, "longitude": longitude, "storeLimit": 20}
        if self.brand == "GS25":
            # The current server validates keyword before applying itemCode's
            # documented search bypass. Keep both without a second search call.
            arguments.update(itemCode=code, keyword=name)
        elif self.brand == "이마트24":
            arguments["pluCd"] = code
            region = self.emart_region or emart24_region_korean(latitude, longitude)
            arguments.update(area1=region[0], area2=region[1])
        elif self.brand == "CU":
            # Public CU check_inventory is keyword/offset based, not code based.
            arguments.update(keyword=name, size=20, offset=0)
        else:
            arguments = {"keyword": name, "storeKeyword": self._region_keyword(latitude, longitude), "storeLimit": 20}
        payload = self._unified("check_inventory", **arguments)
        if payload.get("stockAvailable") is False:
            reason = str(payload.get("stockError") or "재고 제공 경로를 이용할 수 없습니다.")
            raise LiveDataError(reason, {"error": {"code": "INVENTORY_UNAVAILABLE", "message": reason},
                                         "response": self.safe_payload(payload)})
        for section in ("inventory", "nearbyStores"):
            availability = payload.get(section)
            if isinstance(availability, dict) and availability.get("available") is False:
                reason = str(availability.get("unavailableReason") or "재고 조회를 이용할 수 없습니다.")
                raise LiveDataError(reason, {
                    "error": {"code": "INVENTORY_UNAVAILABLE", "message": reason},
                    "response": self.safe_payload(payload),
                })
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if self.brand == "CU":
            inventory = payload.get("inventory") or {}
            nearby = payload.get("nearbyStores") or {}
            items = self._dict_list(inventory.get("items"))
            if inventory.get("available") is False or nearby.get("available") is False:
                raise LiveDataError(str(inventory.get("unavailableReason") or nearby.get("unavailableReason") or "재고 조회를 이용할 수 없습니다."))
            if not items and isinstance(inventory.get("items"), list):
                return []
            reported = str(nearby.get("stockItemCode") or "")
            if reported != code:
                match_index = next((index for index, item in enumerate(items) if str(item.get("itemCode") or "") == code), None)
                if match_index is None:
                    self.lookup_failures.append(code)
                    return []
                payload = self._unified("check_inventory", keyword=name, latitude=latitude, longitude=longitude,
                                        size=1, offset=match_index, storeLimit=20)
                if str((payload.get("nearbyStores") or {}).get("stockItemCode") or "") != code:
                    raise LiveDataError("조회된 재고의 상품 코드가 선택 상품과 다릅니다.")
            rows = self._parse_cu(payload, name, now)
        else:
            # Preserve service-native rows; standard.stores currently omits stock.
            product = payload.get("product") if isinstance(payload.get("product"), dict) else {}
            for node in self._walk(payload):
                if isinstance(node.get("goodsInfo"), dict):
                    product = node["goodsInfo"]
                    break
            reported = str(self._first(payload, "itemCode", "itemCodeUsed", "pluCd", default=self._first(product, "itemCode", "pluCd", "code")) or "")
            if reported and reported != code:
                raise LiveDataError("조회된 재고의 상품 코드가 선택 상품과 다릅니다.")
            metadata = {**product, "itemCode": code, "itemName": name}
            rows = self._extract_inventory_records(payload, name, now, metadata)
            if not rows and not any(isinstance(node.get("stores"), list) for node in self._walk(payload)):
                raise LiveDataError("매장별 재고 목록을 읽지 못했습니다.")
            rows = [row for row in rows if row.product_code == code]
            if not rows and any(node.get("stores") for node in self._walk(payload)
                                if isinstance(node.get("stores"), list)):
                raise LiveDataError("응답에 매장이 있지만 재고 수량이나 상품 코드를 읽지 못했습니다.")
        rows = self._enrich_store_locations(payload, rows, latitude, longitude)
        source_time = self._source_timestamp(payload, "")
        for row in rows:
            row.received_at = now
            row_time = row.stock_source_updated_at or row.stock_updated_at or source_time
            row.source_updated_at = row_time
            row.stock_source_updated_at = row_time
            row.stock_updated_at = row_time
            row.raw_payload = self.safe_payload(payload)
            row.upstream_cache_status = str((payload.get("meta") or {}).get("cacheStatus") or "")
            row.stock_source = "공개 재고 중계 서비스 (MCP)"
        return rows

    @classmethod
    def _enrich_store_locations(cls, payload: dict[str, Any], rows: list[StoreProduct],
                                latitude: float, longitude: float) -> list[StoreProduct]:
        """Join separate location metadata by exact store code, never by order.

        Emart24's inventory.stores contains quantities while nearbyStores.stores
        holds coordinates. Do not manufacture stock rows for metadata-only stores.
        """
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        nearby = data.get("nearbyStores") if isinstance(data.get("nearbyStores"), dict) else {}
        standard = data.get("standard") if isinstance(data.get("standard"), dict) else {}
        candidates = cls._dict_list(nearby.get("stores")) + cls._dict_list(standard.get("stores"))
        stores: dict[str, dict[str, Any]] = {}
        ambiguous: set[str] = set()
        for candidate in candidates:
            raw = candidate.get("raw") if isinstance(candidate.get("raw"), dict) else candidate
            merged = {**candidate, **raw}
            code = str(cls._first(merged, "storeCode", "bizNo", "storeId", "storeCd", "BIZNO", "code", default="") or "").strip()
            if not code:
                continue
            if code in stores:
                old = stores[code]
                old_coords = (cls._first(old, "latitude", "lat", "LATITUDE"), cls._first(old, "longitude", "lng", "LONGITUDE"))
                new_coords = (cls._first(merged, "latitude", "lat", "LATITUDE"), cls._first(merged, "longitude", "lng", "LONGITUDE"))
                if all(value is not None for value in (*old_coords, *new_coords)) and old_coords != new_coords:
                    ambiguous.add(code)
            stores[code] = merged
        for row in rows:
            if row.store_code in ambiguous:
                continue
            store = stores.get(row.store_code)
            if store is None:
                continue
            if row.latitude is None:
                row.latitude = cls._parse_float(cls._first(store, "latitude", "lat", "LATITUDE"))
            if row.longitude is None:
                row.longitude = cls._parse_float(cls._first(store, "longitude", "lng", "LONGITUDE"))
            row.address = row.address or str(cls._first(store, "address", "addr", "roadAddress", "ADDRESS", default="") or "")
            row.store_name = row.store_name or str(cls._first(store, "storeName", "name", "TITLE", default="") or "")
            distance = haversine_km(latitude, longitude, row.latitude, row.longitude)
            if distance is not None:
                row.distance_km = distance
        return rows

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
            raise LiveDataError(str(exc), getattr(exc, "details", {})) from exc

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
                product_name=name, barcode=str(self._first(product, "barcode", "ean", "gtin", "upc", default="") or ""),
                product_code=code, price=self._parse_int(self._first(stock_store, "price", "salePrice", "sellPrice"), price), promotion=promotion, stock=qty,
                image_url=self._product_image(product) or self._product_image(goods),
                stock_quantity_exact=self._stock_quantity_is_exact(qty_raw),
                latitude=store_lat, longitude=store_lng, distance_km=distance,
                data_source="LIVE", updated_at=now, store_code=store_code,
                stock_source="Daiso MCP", price_source="Daiso MCP" if price else "미제공",
                promotion_source="Daiso MCP" if promotion else "미제공",
                stock_updated_at=timestamp, price_updated_at=timestamp if price else "",
                stock_known=known, promotion_known=bool(promotion),
                price_data_status="LIVE" if self._parse_int(self._first(stock_store, "price", "salePrice", "sellPrice"), 0) else "REFERENCE" if price else "UNKNOWN",
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
                self.request_count += 1
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
            store_price = cls._parse_int(cls._first(store, "price", "salePrice", "sellPrice", "searchItemSellPrice"), 0)
            records.append(StoreProduct(
                brand="CU", store_name=str(store.get("storeName") or ""),
                address=str(store.get("address") or ""), product_name=name,
                barcode=str(cls._first(chosen, "barcode", "ean", "gtin", "upc", default="") or ""), product_code=code,
                price=store_price or price, promotion="", stock=qty,
                stock_quantity_exact=cls._stock_quantity_is_exact(stock),
                image_url=cls._product_image(chosen),
                latitude=cls._parse_float(store.get("latitude")), longitude=cls._parse_float(store.get("longitude")),
                distance_km=cls._parse_distance(store.get("distanceM")), data_source="LIVE", updated_at=now,
                store_code=str(store.get("storeCode") or ""), stock_known=known,
                stock_source="Daiso MCP", price_source="매장별 재고 응답" if store_price else "상품 목록 참고 가격", promotion_source="미제공",
                price_data_status="LIVE" if store_price else "REFERENCE" if price else "UNKNOWN",
                promotion_known=False, received_at=now, source_updated_at=cls._source_timestamp(store) or cls._source_timestamp(payload),
                stock_source_updated_at=cls._source_timestamp(store) or cls._source_timestamp(payload),
                stock_updated_at=cls._source_timestamp(store) or cls._source_timestamp(payload),
                raw_payload=cls.safe_payload(payload),
            ))
        return records

    def _extract_inventory_records(self, payload: Any, keyword: str, now: str,
                                   product: dict[str, Any] | None = None) -> list[StoreProduct]:
        found: list[StoreProduct] = []
        source_timestamp = self._source_timestamp(payload, "")
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
            row_source_timestamp = self._source_timestamp(merged) or source_timestamp
            product_name = str(self._first(merged, "productName", "goodsName", "itemName", "name", default=fallback_product_name) or fallback_product_name)
            code = str(self._first(merged, "itemCode", "code", "goodsCode", "pluCd", "barcode", default=fallback_code) or fallback_code)
            price_raw = self._first(merged, "price", "salePrice", "sellPrice", "searchItemSellPrice",
                                    "sellingPrice", "priceToPay", "finalPrice", "viewPrice")
            promo = str(self._first(merged, "promotion", "event", "eventName", "promotionName", "eventText", default="") or "")
            found.append(StoreProduct(
                brand=self.brand, store_name=str(name or ""), address=str(address or ""),
                product_name=product_name,
                barcode=str(self._first(merged, "barcode", "ean", "gtin", "upc", default=self._first(product or {}, "barcode", "ean", "gtin", "upc", default="")) or ""),
                product_code=code, price=self._parse_int(price_raw, fallback_price),
                image_url=self._product_image(product or {}) or self._product_image(merged),
                promotion=promo, stock=stock, stock_quantity_exact=self._stock_quantity_is_exact(stock_raw),
                latitude=self._parse_float(self._first(merged, "latitude", "lat", "storeLat")),
                longitude=self._parse_float(self._first(merged, "longitude", "lng", "lon", "storeLng")),
                distance_km=self._parse_distance(self._first(merged, "distanceKm", "distanceM", "distance", "distance_km")),
                data_source="LIVE", updated_at=now,
                store_code=str(store_code or ""),
                received_at=now, source_updated_at=row_source_timestamp, stock_source_updated_at=row_source_timestamp,
                raw_payload=self.safe_payload(payload), stock_updated_at=row_source_timestamp,
                price_updated_at=row_source_timestamp if fallback_price or price_raw else "",
                stock_known=known, stock_source="Daiso MCP", price_source="Daiso MCP",
                price_data_status="LIVE" if self._parse_int(price_raw, 0) else "REFERENCE" if fallback_price else "UNKNOWN",
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
    def _source_timestamp(payload: Any, fallback: str = "") -> str:
        # HTTP Date/Age and relay fetchedAt are transport/cache times, not the
        # original stock observation time. Never synthesize a source timestamp.
        if isinstance(payload, dict):
            nodes = [node for node in (payload, payload.get("meta") or {}, payload.get("inventory") or {})
                     if isinstance(node, dict)]
            # A stock/source clock outranks a generic wrapper updatedAt.
            for key in ("stockUpdatedAt", "sourceUpdatedAt", "updatedAt"):
                for node in nodes:
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return ""

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
