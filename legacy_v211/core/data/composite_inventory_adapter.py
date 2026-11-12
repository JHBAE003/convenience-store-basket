from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
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
from core.services.distance import haversine_km


class CompositeInventoryAdapter:
    """Second inventory track using official stock APIs and documented MCP tools."""

    TRACK_NAME = "Daiso MCP 통합조회"
    SEVEN_DIRECT_BASE = "https://new.7-elevenapp.co.kr"
    SEVEN_DIRECT_STORE_LIMIT = 3

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
        self.request_count = 0
        self.inner_adapter: DaisoMcpAdapter | None = None
        self.selected_stores: list[dict[str, Any]] | None = None
        self.coverage_note = ""

    def check_selected(self, link: Any, latitude: float, longitude: float) -> list[StoreProduct]:
        """Recovery for a selected code; do not repeat broad candidate searches."""
        if self.brand == "세븐일레븐":
            return self._check_seven_direct(link, latitude, longitude)
        adapter = DaisoMcpAdapter(self.brand, emart_region=self.emart_region)
        self.inner_adapter = adapter
        code, name = str(link.product_code), str(link.product_name)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        if self.brand == "이마트24":
            if self.selected_stores is None:
                stores = adapter._emart_stores(latitude, longitude)
            else:
                stores = {}
                for standard in self.selected_stores:
                    raw = standard.get("raw") if isinstance(standard.get("raw"), dict) else standard
                    key = str(DaisoMcpAdapter._first(standard, "code", default=DaisoMcpAdapter._first(raw, "bizNo", "storeCode", "BIZNO", "CODE")) or "")
                    if key:
                        stores[key] = raw
            if not stores:
                return []
            response = None
            for attempt in range(2):
                self.request_count += 1
                try:
                    response = requests.get(
                        "https://everse.emart24.co.kr/api/stock/v3/stock-search/store",
                        params={"searchPluCode": code, "bizNoArr": ",".join(stores)},
                        headers={"x-requested-with": "XMLHttpRequest"}, timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    if response.status_code >= 500 and attempt == 0:
                        import time
                        time.sleep(.35)
                        continue
                    response.raise_for_status()
                    break
                except (requests.Timeout, requests.ConnectionError):
                    if attempt:
                        raise
                    import time
                    time.sleep(.35)
            payload = response.json()
            if (not isinstance(payload, dict) or not isinstance(payload.get("storeGoodsInfo"), dict)
                    or not isinstance(payload.get("storeGoodsQty"), list)):
                raise LiveDataError("이마트24 공식 재고 응답 형식이 올바르지 않습니다.")
            normalized = {"pluCd": code, "inventory": {
                "goodsInfo": payload["storeGoodsInfo"],
                "stores": [{"bizNo": item.get("BIZNO"), "bizQty": item.get("BIZQTY")}
                           for item in payload["storeGoodsQty"] if isinstance(item, dict)]}}
            rows = adapter._parse_emart_inventory(normalized, {"pluCd": code, "goodsName": name},
                                                   stores, latitude, longitude, now)
        elif self.brand == "CU":
            payload = adapter._rest("/api/cu/inventory", {"keyword": name, "lat": latitude,
                                                         "lng": longitude, "size": 20, "offset": 0, "storeLimit": 20})
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            inventory = data.get("inventory") or {}
            if inventory.get("available") is False:
                raise LiveDataError(str(inventory.get("unavailableReason") or "재고 조회 불가"))
            items = DaisoMcpAdapter._dict_list(inventory.get("items"))
            if not items and isinstance(inventory.get("items"), list):
                return []
            if str((data.get("nearbyStores") or {}).get("stockItemCode") or "") != code:
                offset = next((i for i, row in enumerate(items) if str(row.get("itemCode") or "") == code), None)
                if offset is None:
                    self.lookup_failures.append(code)
                    return []
                payload = adapter._rest("/api/cu/inventory", {"keyword": name, "lat": latitude,
                                                             "lng": longitude, "size": 1, "offset": offset, "storeLimit": 20})
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                if str((data.get("nearbyStores") or {}).get("stockItemCode") or "") != code:
                    raise LiveDataError("재고 상품 코드가 선택 상품과 다릅니다.")
            rows = DaisoMcpAdapter._parse_cu(data, name, now)
        else:
            path = "/api/gs25/inventory" if self.brand == "GS25" else "/api/seveneleven/inventory"
            arguments = ({"itemCode": code, "keyword": name} if self.brand == "GS25" else
                         {"keyword": name, "storeKeyword": adapter._region_keyword(latitude, longitude)})
            payload = adapter._rest(path, {**arguments, "lat": latitude, "lng": longitude, "storeLimit": 20})
            rows = adapter._extract_inventory_records(payload, name, now, {"itemCode": code, "itemName": name})
            if not rows and not any(isinstance(node.get("stores"), list) for node in DaisoMcpAdapter._walk(payload)):
                raise LiveDataError("매장별 재고 목록을 읽지 못했습니다.")
            if any(row.product_code != code for row in rows):
                raise LiveDataError("재고 상품 코드가 선택 상품과 다릅니다.")
        # The manager also reads inner_adapter after failures. Count before an
        # exception must not disappear merely because this method did not return.
        rows = DaisoMcpAdapter._enrich_store_locations(payload, rows, latitude, longitude)
        source_time = DaisoMcpAdapter._source_timestamp(payload)
        for row in rows:
            row.received_at = now
            row_time = row.stock_source_updated_at or row.stock_updated_at or source_time
            row.stock_updated_at = row_time
            row.stock_source_updated_at = row_time
            row.source_updated_at = row_time
            row.raw_payload = DaisoMcpAdapter.safe_payload(payload)
            row.stock_source = ("이마트24 공식 재고 API (보조)" if self.brand == "이마트24"
                                else "공개 재고 중계 서비스 (REST 보조)")
        return rows

    def _seven_json(self, method: str, path: str, *, params=None, body=None) -> dict[str, Any]:
        """One public request per step; never bypass access/authentication checks."""
        self.request_count += 1
        headers = {"Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
                   "User-Agent": "Mozilla/5.0 (Linux; Android 15)"}
        try:
            if method == "GET":
                response = requests.get(self.SEVEN_DIRECT_BASE + path, params=params, headers=headers,
                                        timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False)
            else:
                response = requests.post(self.SEVEN_DIRECT_BASE + path, json=body, headers=headers,
                                         timeout=REQUEST_TIMEOUT_SECONDS, allow_redirects=False)
        except requests.RequestException as exc:
            raise LiveDataError("세븐일레븐 공식 조회 연결을 확인하지 못했습니다.", {
                "error": {"code": "SEVEN_DIRECT_NETWORK", "message": type(exc).__name__},
                "diagnostics": {"retryable": True},
            }) from exc
        status = response.status_code
        if status != 200:
            try:
                error_payload = DaisoMcpAdapter.safe_payload(response.json())
            except ValueError:
                error_payload = {"body_type": "JSON 아님"}
            raise LiveDataError(f"세븐일레븐 공식 조회가 거절되거나 실패했습니다. (HTTP {status})", {
                "error": {"code": "SEVEN_DIRECT_HTTP", "message": f"HTTP {status}"},
                "diagnostics": {"status": status, "upstreamStatus": status, "retryable": status >= 500},
                "response": error_payload,
            })
        try:
            payload = response.json()
        except ValueError as exc:
            raise LiveDataError("세븐일레븐 공식 조회가 정상 JSON을 제공하지 않았습니다.", {
                "error": {"code": "SEVEN_DIRECT_NOT_JSON"}, "diagnostics": {"status": status},
            }) from exc
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise LiveDataError("세븐일레븐 공식 조회가 오류 응답을 제공했습니다.", {
                "error": {"code": "SEVEN_DIRECT_RESPONSE"},
                "response": DaisoMcpAdapter.safe_payload(payload),
            })
        return payload

    def _check_seven_direct(self, link: Any, latitude: float, longitude: float) -> list[StoreProduct]:
        """Selected-code official recovery, bounded to the nearest three stores."""
        code = str(link.product_code).strip()
        if not code:
            raise LiveDataError("세븐일레븐 선택 상품 코드가 없습니다.")
        metadata_payload = self._seven_json("GET", "/api/v1/open/product/search/stock", params={"itemCd": code})
        metadata = metadata_payload.get("data") if isinstance(metadata_payload.get("data"), dict) else metadata_payload
        if str(metadata.get("itemCd") or "") != code:
            raise LiveDataError("세븐일레븐 공식 상품 코드가 선택 상품과 다릅니다.", {
                "error": {"code": "SEVEN_PRODUCT_MISMATCH"}, "response": DaisoMcpAdapter.safe_payload(metadata_payload)})
        if not all(str(metadata.get(key) or "").strip() for key in ("smCd", "stokMngCd")):
            raise LiveDataError("세븐일레븐 공식 상품의 재고 관리 정보를 확인하지 못했습니다.")
        if any(metadata.get(key) is None for key in ("stokMngQty", "stockApplicationRate")):
            raise LiveDataError("세븐일레븐 공식 상품의 재고 관리 수량 정보를 확인하지 못했습니다.")
        if self.selected_stores is None:
            location = DaisoMcpAdapter("세븐일레븐", emart_region=self.emart_region)._region_keyword(latitude, longitude)
            store_payload = self._seven_json("POST", "/api/v1/open/search/store", body={
                "collection": "store", "query": location, "sort": "Date/desc", "listCount": 100})
            data = store_payload.get("data") if isinstance(store_payload.get("data"), dict) else store_payload
            query = data.get("SearchQueryResult")
            if not isinstance(query, dict) or not isinstance(query.get("Collection"), list):
                raise LiveDataError("세븐일레븐 공식 점포 목록을 해석하지 못했습니다.")
            stores = []
            for collection in query["Collection"]:
                documents = (collection.get("Documentset") or {}).get("Document", []) if isinstance(collection, dict) else []
                stores.extend(document["field"] for document in documents
                              if isinstance(document, dict) and isinstance(document.get("field"), dict))
            self.selected_stores = DaisoMcpAdapter.safe_payload(stores)
        candidates = {}
        for saved in self.selected_stores:
            store = saved.get("field") if isinstance(saved.get("field"), dict) else saved
            store_code = str(DaisoMcpAdapter._first(store, "storeCd", "storeCode", "code", default="") or "").strip()
            if not store_code or store.get("storeCloseYn") == "Y":
                continue
            lat = DaisoMcpAdapter._parse_float(DaisoMcpAdapter._first(store, "storeLat", "latitude", "lat"))
            lng = DaisoMcpAdapter._parse_float(DaisoMcpAdapter._first(store, "storeLon", "longitude", "lng"))
            distance = haversine_km(latitude, longitude, lat, lng)
            if distance is not None:
                candidates.setdefault(store_code, (distance, store, lat, lng))
        selected = sorted(candidates.items(), key=lambda pair: (pair[1][0], pair[0]))[:self.SEVEN_DIRECT_STORE_LIMIT]
        self.catalog_incomplete = len(candidates) > len(selected)
        self.coverage_note = f"세븐일레븐 공식 보조: 가까운 {len(selected)}개 점포 확인 (받은 지역 목록 {len(self.selected_stores)}곳 중)"
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        rows = []
        for store_code, (distance, store, lat, lng) in selected:
            stock_payload, error, quantity, known, exact = {}, None, 0, False, False
            try:
                stock_payload = self._seven_json("POST", "/api/v1/open/real-stock/multi/01/stocks", body={
                    "smCd": metadata["smCd"], "stokMngCd": metadata["stokMngCd"],
                    "stokMngQty": metadata.get("stokMngQty"), "stockApplicationRate": metadata.get("stockApplicationRate"),
                    "storeList": [store_code]})
                data = stock_payload.get("data") if isinstance(stock_payload.get("data"), dict) else stock_payload
                if str(data.get("smCd") or "") != str(metadata["smCd"]):
                    raise LiveDataError("세븐일레븐 공식 재고의 상품 관리 코드가 다릅니다.")
                matches = [item for item in DaisoMcpAdapter._dict_list(data.get("storeList"))
                           if str(item.get("storeCd") or "") == store_code]
                values = {str(item.get("stock")) for item in matches}
                if len(values) != 1:
                    raise LiveDataError("세븐일레븐 공식 점포 재고가 누락되었거나 충돌합니다.")
                raw = matches[0].get("stock")
                quantity, known = DaisoMcpAdapter._parse_stock(raw, matches[0])
                exact = DaisoMcpAdapter._stock_quantity_is_exact(raw)
                try:
                    if float(raw) < 0:
                        quantity, known, exact = 0, False, False
                except (TypeError, ValueError):
                    pass
                if not known:
                    raise LiveDataError("세븐일레븐 공식 재고 수량을 확인하지 못했습니다.")
            except (LiveDataError, ValueError, TypeError) as exc:
                self.lookup_failures.append(store_code)
                error = DaisoMcpAdapter.safe_payload(getattr(exc, "details", {}) or {"message": str(exc)})
                self.catalog_incomplete = True
            address = " ".join(dict.fromkeys(str(store.get(key) or "").strip()
                                             for key in ("addr1", "addr2") if str(store.get(key) or "").strip()))
            price = DaisoMcpAdapter._parse_int(metadata.get("onlinePrice"), 0)
            rows.append(StoreProduct("세븐일레븐", str(store.get("storeNm") or store.get("storeName") or ""),
                address or str(store.get("address") or ""), str(metadata.get("itemOnm") or link.product_name),
                product_code=code, store_code=store_code, price=price, stock=quantity,
                stock_known=known, stock_quantity_exact=exact, latitude=lat, longitude=lng, distance_km=distance,
                data_source="LIVE", updated_at=now, received_at=now,
                stock_data_status="LIVE" if known else "ERROR", price_data_status="REFERENCE" if price else "UNKNOWN",
                promotion=str(metadata.get("evnMnmNm") or ""), promotion_known=False,
                stock_source="세븐일레븐 공식 재고 API (직접 보조)",
                price_source="세븐일레븐 공식 상품 참고 가격" if price else "미제공",
                promotion_source="세븐일레븐 공식 상품 정보 (조건 미확인)",
                raw_payload={"product": DaisoMcpAdapter.safe_payload(metadata_payload),
                             "store": DaisoMcpAdapter.safe_payload(store),
                             "inventory": DaisoMcpAdapter.safe_payload(stock_payload), "error": error}))
            if error and (error.get("diagnostics") or {}).get("status") in (401, 403, 429):
                break
        if self.lookup_failures:
            self.coverage_note += f" · 재고 미확인 {len(self.lookup_failures)}곳"
        if len(rows) < len(selected):
            self.coverage_note += f" · 접근 제한으로 선택 {len(selected)}곳 중 {len(rows)}곳에서 중단"
        if rows and not any(row.stock_known for row in rows):
            errors = [{"store_code": row.store_code, "details": row.raw_payload.get("error") or {}}
                      for row in rows]
            diagnostics = next((item["details"]["diagnostics"] for item in errors
                                if isinstance(item["details"].get("diagnostics"), dict)), {})
            raise LiveDataError("세븐일레븐 공식 보조에서 선택 점포의 재고를 모두 확인하지 못했습니다.", {
                "error": {"code": "SEVEN_DIRECT_STOCK_FAILED"}, "diagnostics": diagnostics,
                "product": DaisoMcpAdapter.safe_payload(metadata_payload),
                "stores": DaisoMcpAdapter.safe_payload(self.selected_stores),
                "errors": errors, "inventory_results": [row.raw_payload for row in rows],
                "coverage_note": self.coverage_note,
            })
        return rows

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
            raise LiveDataError(f"{self.brand} {self.TRACK_NAME} 실패: {exc}", getattr(exc, "details", {})) from exc

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
                barcode=str(DaisoMcpAdapter._first(product, "barcode", "ean", "gtin", "upc", default="") or ""),
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
                price_data_status="LIVE" if DaisoMcpAdapter._parse_int(store.get("searchItemSellPrice"), 0) else "REFERENCE" if price else "UNKNOWN",
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
                barcode=str(DaisoMcpAdapter._first(product, "barcode", "ean", "gtin", "upc", default="") or ""),
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
                price_data_status="LIVE" if DaisoMcpAdapter._parse_int(store.get("searchItemSellPrice"), 0) else "REFERENCE" if price else "UNKNOWN",
                stock_data_status="LIVE" if known else "UNKNOWN",
                promotion_data_status="LIVE" if store.get("eventName") else "UNKNOWN",
            ))
        return rows

    @staticmethod
    def _tag(rows: list[StoreProduct], now: str) -> list[StoreProduct]:
        for row in rows:
            row.stock_source = "Daiso MCP REST 통합조회 (Track 2)"
            row.stock_updated_at = row.stock_updated_at or now
        return rows

    @staticmethod
    def _source_timestamp(result: dict[str, Any], fallback: str = "") -> str:
        return DaisoMcpAdapter._source_timestamp(result)


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
            if (previous.stock_known and row.stock_known
                    and (previous.stock, previous.stock_quantity_exact) != (row.stock, row.stock_quantity_exact)
                    and _freshness_key(row, track_name)[0] == _freshness_key(previous, previous_track)[0]):
                # Equal or missing source clocks cannot establish which of two
                # conflicting observations is newer. Keep one identity, retain
                # both raw facts and exclude it from confirmed recommendations.
                chosen[existing_index] = (previous_track, replace(
                    previous, stock_known=False, stock_quantity_exact=False,
                    stock_data_status="PARTIAL", data_source="PARTIAL",
                    raw_payload={"inventory_conflict": True, "observations": [
                        {"stock": previous.stock, "quantity_exact": previous.stock_quantity_exact, "raw": previous.raw_payload},
                        {"stock": row.stock, "quantity_exact": row.stock_quantity_exact, "raw": row.raw_payload}]}))
                continue
            if _freshness_key(row, track_name) > _freshness_key(previous, previous_track):
                chosen[existing_index] = (track_name, row)
    return [row for _, row in chosen]


def _freshness_key(row: StoreProduct, track_name: str) -> tuple[datetime, int, int]:
    raw_timestamp = row.stock_source_updated_at or row.stock_updated_at
    try:
        observed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = datetime.min.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        observed = datetime.min.replace(tzinfo=timezone.utc)
    # At equal timestamps prefer a confirmed stock value, then Track 2.
    return observed, int(row.stock_known), int("보조" not in track_name and "Track 2" not in track_name)
