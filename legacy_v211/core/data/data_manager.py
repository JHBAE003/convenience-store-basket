from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Any
import json
import threading
import time

from core.config import (BRANDS, LIVE_CACHE_TTL_SECONDS, PRODUCT_CACHE_TTL_SECONDS,
                         STORE_CACHE_TTL_SECONDS, FAILURE_COOLDOWN_SECONDS)
from core.data.csv_adapter import CsvAdapter
from core.data.composite_inventory_adapter import CompositeInventoryAdapter, merge_inventory_tracks
from core.data.live_mcp_adapter import DaisoMcpAdapter, LiveDataError
from core.data.promotion_manager import PromotionManager
from core.models import DataLoadResult, StoreProduct
from core.services.integration_service import integrate_records

_SANITIZE = DaisoMcpAdapter.safe_payload


class DataManager:
    """Session-local caches and shared in-flight requests; no global query state."""
    LIVE_CACHE_VERSION = 4
    EMART24_CACHE_VERSION = 2
    VALID_MODES = {"TEST", "LIVE"}

    def __init__(self, test_csv: str | Path, mode: str = "TEST", log: Callable[[str], None] | None = None,
                 cache_path: str | Path | None = None, cache_ttl_seconds: int = LIVE_CACHE_TTL_SECONDS,
                 promotion_manager: PromotionManager | None = None,
                 product_cache_ttl_seconds: int = PRODUCT_CACHE_TTL_SECONDS,
                 store_cache_ttl_seconds: int = STORE_CACHE_TTL_SECONDS):
        self.test_csv = Path(test_csv)
        self.mode = self._mode(mode)
        self.log = log or (lambda message: None)
        self.test_adapter = CsvAdapter(self.test_csv)
        self.promotion_manager = promotion_manager if promotion_manager is not None else PromotionManager()
        self.cache_ttl_seconds = max(1, cache_ttl_seconds)
        self.product_cache_ttl_seconds = max(1, product_cache_ttl_seconds)
        self.store_cache_ttl_seconds = max(1, store_cache_ttl_seconds)
        self.cache_path = Path(cache_path) if cache_path else self.test_csv.with_name("live_cache.json")
        self._last_good: dict[tuple, tuple[float, list[StoreProduct]]] = {}
        self._cache_issues: dict[tuple, str] = {}
        self._product_cache: dict[tuple, tuple[float, list[StoreProduct], str]] = {}
        self._store_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._region_cache: dict[tuple, tuple[float, tuple[str, str]]] = {}
        self._flights: dict[tuple, Future] = {}
        self._cooldowns: dict[tuple, tuple[float, Any, BaseException | None]] = {}
        self._failures: dict[tuple, tuple[float, DataLoadResult]] = {}
        self._cache_lock = threading.RLock()
        self._metrics_lock = threading.RLock()
        self.metrics: list[dict[str, Any]] = []
        self._load_cache()

    def _mode(self, mode):
        active_mode = (mode or getattr(self, "mode", "TEST")).upper()
        if active_mode not in self.VALID_MODES:
            raise ValueError(f"지원하지 않는 데이터 모드: {active_mode}")
        return active_mode

    def _shared(self, key: tuple, operation):
        with self._cache_lock:
            # A forced refresh bypasses data caches, never the short failure
            # cooldown. Its key otherwise retains every query condition.
            cooldown_key = key[:-1]
            cooldown = self._cooldowns.get(cooldown_key)
            if cooldown and 0 <= time.monotonic() - cooldown[0] < FAILURE_COOLDOWN_SECONDS:
                if cooldown[2] is not None:
                    raise cooldown[2]
                return deepcopy(cooldown[1])
            future = self._flights.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._flights[key] = future
        if not owner:
            return deepcopy(future.result())
        try:
            result = operation()
            if isinstance(result, DataLoadResult) and result.source in {"ERROR", "CACHE"}:
                with self._cache_lock:
                    self._cooldowns[cooldown_key] = (time.monotonic(), deepcopy(result), None)
            future.set_result(deepcopy(result))
            return result
        except BaseException as exc:
            with self._cache_lock:
                self._cooldowns[cooldown_key] = (time.monotonic(), None, exc)
            future.set_exception(exc)
            raise
        finally:
            with self._cache_lock:
                self._flights.pop(key, None)

    @staticmethod
    def _cache_key(brand: str, keyword: str, lat: float, lng: float, code: str = "", barcode: str = "") -> tuple:
        # Do not round coordinates: different conditions must not share results.
        return brand, keyword.casefold().strip(), float(lat), float(lng), str(code), str(barcode)

    def search_all(self, keyword: str, user_lat: float, user_lng: float, brands: list[str] | None = None,
                   mode: str | None = None, *, include_unpriced: bool = False, force_refresh: bool = False):
        selected = BRANDS if brands is None else brands
        active = self._mode(mode)
        def load(brand):
            key = self._cache_key(brand, keyword, user_lat, user_lng)
            return self._shared(("inventory", active, key, force_refresh),
                                lambda: self._search_brand(brand, keyword, user_lat, user_lng, active, force_refresh))
        return self._filter_results(self._brand_map(load, selected, active), include_unpriced)

    @staticmethod
    def _brand_map(operation, brands, mode):
        if mode == "LIVE" and len(brands) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(brands))) as pool:
                return list(pool.map(operation, brands))
        return [operation(brand) for brand in brands]

    def search_selected(self, link, user_lat: float, user_lng: float, mode: str | None = None,
                        *, include_unpriced: bool = True, force_refresh: bool = False):
        active = self._mode(mode)
        key = self._cache_key(link.brand, link.product_name, user_lat, user_lng, link.product_code, link.barcode)
        result = self._shared(("inventory", active, key, force_refresh),
                              lambda: self._search_brand(link.brand, link.product_name, user_lat, user_lng,
                                                         active, force_refresh, link))
        return self._filter_results([result], include_unpriced)

    check_selected = search_selected

    @staticmethod
    def _filter_results(results, include_unpriced):
        all_records, statuses = [], []
        for result in results:
            rows = [row for row in result.records if row.has_store_identity and (include_unpriced or row.price > 0)]
            missing = sum(row.has_store_identity and row.price <= 0 for row in result.records)
            message = result.message
            if missing and not include_unpriced:
                message += f" 가격 미확인 상품 {missing}건을 결과에서 제외했습니다."
            statuses.append(replace(result, records=rows, message=message))
            all_records.extend(rows)
        return all_records, statuses

    def search_products(self, keyword: str, user_lat: float, user_lng: float,
                        brands: list[str] | None = None, mode: str | None = None, *, force_refresh: bool = False):
        active = self._mode(mode)
        selected = BRANDS if brands is None else brands
        def load(brand):
            key = self._cache_key(brand, keyword, user_lat, user_lng)
            return self._shared(("products", active, key, force_refresh),
                                lambda: self._search_products_brand(brand, keyword, user_lat, user_lng, active, force_refresh))
        results = self._brand_map(load, selected, active)
        return [row for result in results for row in result.records], results

    def _search_products_brand(self, brand, keyword, lat, lng, mode, force):
        if mode == "TEST":
            rows = [row for row in self.test_adapter.search(keyword, lat, lng) if row.brand == brand]
            self._mark_test(rows)
            unique = {}
            for row in rows:
                unique.setdefault(row.product_code or row.barcode or row.product_name, row)
            rows = [replace(row, store_name="", address="", store_code="", stock_known=False,
                            price_data_status="REFERENCE") for row in unique.values()]
            return DataLoadResult(brand, rows, "TEST", "시연 상품 후보입니다.")
        key = self._cache_key(brand, keyword, lat, lng)
        with self._cache_lock:
            cached = self._product_cache.get(key)
        if cached and not force and 0 <= time.time() - cached[0] <= self.product_cache_ttl_seconds:
            age = time.time() - cached[0]
            return DataLoadResult(brand, self._cached_rows(cached[1], age), "PARTIAL" if cached[2] else "LIVE",
                                  cached[2] or "저장한 상품 후보입니다. 재고는 상품 선택 후 확인합니다.", age)
        started = time.perf_counter()
        adapter = DaisoMcpAdapter(brand)
        try:
            rows = adapter.search_products(keyword, lat, lng)
            issue = "상품 후보의 첫 페이지입니다. 정확한 상품명으로 범위를 좁혀 주세요." if adapter.catalog_incomplete else ""
            with self._cache_lock:
                self._product_cache[key] = (time.time(), deepcopy(rows), issue)
            metric = self._record_metric(brand, "상품", adapter, started, True, False)
            return DataLoadResult(brand, rows, "PARTIAL" if issue else "LIVE", issue or "상품 후보입니다. 재고는 상품 선택 후 확인합니다.",
                                  request_count=metric["request_count"], elapsed_ms=metric["elapsed_ms"])
        except Exception as exc:
            self._record_metric(brand, "상품", adapter, started, False, False, exc)
            return self._error_result(brand, exc, "상품 후보를 불러오지 못했습니다.")

    def find_stores(self, brand: str, user_lat: float, user_lng: float, *, force_refresh: bool = False):
        key = (brand, float(user_lat), float(user_lng))
        def load():
            with self._cache_lock:
                cached = self._store_cache.get(key)
            if cached and not force_refresh and 0 <= time.time() - cached[0] <= self.store_cache_ttl_seconds:
                return deepcopy(cached[1])
            adapter = DaisoMcpAdapter(brand)
            if brand in {"이마트24", "세븐일레븐"}:
                adapter.emart_region = self._resolve_region(user_lat, user_lng)
            started = time.perf_counter()
            try:
                rows = adapter.find_stores(user_lat, user_lng)
                with self._cache_lock:
                    self._store_cache[key] = (time.time(), deepcopy(rows))
                self._record_metric(brand, "매장", adapter, started, True, False)
                return rows
            except Exception as exc:
                self._record_metric(brand, "매장", adapter, started, False, False, exc)
                raise
        return self._shared(("stores", key, force_refresh), load)

    def _resolve_region(self, lat, lng):
        key = (float(lat), float(lng))
        def load():
            with self._cache_lock:
                cached = self._region_cache.get(key)
            if cached and 0 <= time.time()-cached[0] <= self.store_cache_ttl_seconds:
                return cached[1]
            from core.services.geocoding import emart24_region_korean
            region = emart24_region_korean(lat, lng)
            with self._cache_lock:
                self._region_cache[key] = (time.time(), region)
            return region
        return self._shared(("region", key, False), load)

    @staticmethod
    def _cached_rows(rows, age):
        return [replace(row, app_cache_used=True) for row in deepcopy(rows)]

    def _search_brand(self, brand, keyword, lat, lng, mode=None, force_refresh=False, link=None):
        if self._mode(mode) == "TEST":
            rows = [row for row in self.test_adapter.search(keyword, lat, lng) if row.brand == brand]
            if link:
                rows = [row for row in rows if (row.product_code == link.product_code if link.product_code else row.barcode == link.barcode)]
            self._mark_test(rows)
            return DataLoadResult(brand, rows, "TEST", "시연용 가상 자료입니다.")
        key = self._cache_key(brand, keyword, lat, lng, getattr(link, "product_code", ""), getattr(link, "barcode", ""))
        with self._cache_lock:
            cached = self._last_good.get(key)
            recent_failure = self._failures.get(key)
        if cached and not force_refresh:
            saved_at, saved = cached
            age = time.time() - saved_at
            if 0 <= age <= self.cache_ttl_seconds:
                issue = self._cache_issues.get(key, "")
                return DataLoadResult(brand, self._cached_rows(saved, age), "PARTIAL" if issue else "LIVE",
                                      (issue + " " if issue else "") + f"{self.cache_ttl_seconds}초 이내 저장 결과입니다. 원본 자료 시각은 그대로 유지합니다.", age)
        if recent_failure and 0 <= time.monotonic() - recent_failure[0] < FAILURE_COOLDOWN_SECONDS:
            return deepcopy(recent_failure[1])
        try:
            records, issue, metrics, error_details = self._query_inventory(brand, keyword, lat, lng, link, force_refresh)
            records = [row for row in records if row.has_store_identity]
            if link and link.product_code:
                records = [row for row in records if row.product_code == link.product_code]
            promotion_message = ""
            try:
                # 화면의 재고 재조회는 별도의 행사 목록 캐시까지 무효화하지 않는다.
                # 장바구니 여러 상품마다 같은 브랜드 목록을 다시 수집하지 않도록 한다.
                promotions = self.promotion_manager.search(brand, keyword) if records else []
                if promotions:
                    records = integrate_records(records, promotions)
                if records and hasattr(self.promotion_manager, "status_for"):
                    event_status = self.promotion_manager.status_for(brand, keyword)
                    if event_status.get("status") in {"PARTIAL", "UNKNOWN", "ERROR"}:
                        promotion_message = event_status.get("message") or "행사 조건을 확인하지 못했습니다."
            except Exception:
                promotion_message = "행사 정보를 확인하지 못했습니다."
                for row in records:
                    if not row.promotion:
                        row.promotion_known = False
                        row.promotion_data_status = "ERROR"
            for row in records:
                row.data_source = "LIVE"
                row.stock_data_status = "LIVE" if row.stock_known else "UNKNOWN"
                if row.price and row.price_data_status == "UNKNOWN":
                    row.price_data_status = "REFERENCE"
                if not row.promotion and row.promotion_data_status == "UNKNOWN":
                    row.promotion_known = False
                row.received_at = row.received_at or row.updated_at
                row.source_updated_at = row.source_updated_at or row.stock_source_updated_at or row.stock_updated_at
                row.stock_source_updated_at = row.stock_source_updated_at or row.stock_updated_at
            with self._cache_lock:
                self._last_good[key] = (time.time(), deepcopy(records))
                self._cache_issues[key] = issue
                self._failures.pop(key, None)
                try:
                    self._save_cache()
                except OSError:
                    self.log("조회 결과를 임시 저장하지 못했습니다.")
            message = "선택한 상품의 매장별 재고를 확인했습니다." if link else "매장별 재고를 확인했습니다."
            if not records:
                message = "정상 응답에서 점포 재고 결과가 없습니다. 재고 0으로 단정하지 않습니다."
            message += (" " + issue if issue else "") + (" " + promotion_message if promotion_message else "")
            return DataLoadResult(brand, records, "PARTIAL" if issue else "LIVE", message,
                                  request_count=sum(m["request_count"] for m in metrics),
                                  elapsed_ms=sum(m["elapsed_ms"] for m in metrics),
                                  fallback_used=any(m["fallback_used"] for m in metrics), error_details=error_details)
        except Exception as exc:
            result = self._error_result(brand, exc, "실시간 재고를 확인하지 못했습니다.")
            if cached:
                saved_at, saved = cached
                age = max(0, time.time()-saved_at)
                rows = [replace(row, data_source="CACHE", stock_data_status="CACHE",
                                price_data_status="CACHE" if row.price else "UNKNOWN",
                                promotion_data_status="CACHE" if row.promotion else "UNKNOWN")
                        for row in self._cached_rows(saved, age)]
                result = replace(result, records=rows, source="CACHE", cache_age_seconds=age,
                                 message=result.message + " 이전 자료를 참고용으로 표시합니다.")
            with self._cache_lock:
                self._failures[key] = (time.monotonic(), deepcopy(result))
            return result

    def _query_inventory(self, brand, keyword, lat, lng, link=None, force_refresh=False):
        tracks, metrics, failures, failure_details, coverage_notes = [], [], [], [], []
        unresolved, incomplete, primary_error = set(), False, None
        for fallback, cls in ((False, DaisoMcpAdapter), (True, CompositeInventoryAdapter)):
            if fallback and not (primary_error or unresolved or incomplete):
                break
            started, adapter = time.perf_counter(), None
            official_store_key, used_official_store_cache = None, False
            try:
                adapter = cls(brand)
                if link and brand in {"이마트24","세븐일레븐"}:
                    adapter.emart_region = self._resolve_region(lat, lng)
                if fallback and brand=='세븐일레븐' and link:
                    official_store_key=('seven_official',float(lat),float(lng),*adapter.emart_region)
                    with self._cache_lock:
                        saved_stores=self._store_cache.get(official_store_key)
                    if saved_stores and 0<=time.time()-saved_stores[0]<=self.store_cache_ttl_seconds:
                        adapter.selected_stores=deepcopy(saved_stores[1])
                        used_official_store_cache=True
                if fallback and brand == "이마트24" and link:
                    adapter.selected_stores = self.find_stores(brand, lat, lng, force_refresh=force_refresh)
                rows = adapter.check_selected(link, lat, lng) if link else adapter.search(keyword, lat, lng)
                scope_note=getattr(adapter,'coverage_note','')
                if isinstance(scope_note,str) and scope_note:
                    coverage_notes.append(scope_note)
                rows = self._mark_track_observation(rows, "보조" if fallback else "주")
                tracks.append(("보조" if fallback else "주", rows))
                unresolved.update(str(code) for code in getattr(adapter, "lookup_failures", []) if code)
                unresolved.difference_update(row.product_code for row in rows if row.stock_known)
                incomplete = bool(getattr(adapter, "catalog_incomplete", False)) or any(not row.stock_known for row in rows)
                metrics.append(self._record_metric(brand, "재고 보조" if fallback else "재고 주", adapter, started, True, fallback))
            except Exception as exc:
                failures.append(exc)
                detail = _SANITIZE(getattr(exc, "details", {}) or {"message": str(exc)})
                failure_details.append({"path": "보조" if fallback else "주", "error_type": type(exc).__name__, "details": detail})
                if not fallback:
                    primary_error = exc
                metrics.append(self._record_metric(brand, "재고 보조" if fallback else "재고 주", adapter, started, False, fallback, exc))
                details = getattr(exc, "details", {})
                diagnostics = details.get("diagnostics") or {} if isinstance(details, dict) else {}
                if not fallback and (diagnostics.get("status") == 429 or diagnostics.get("upstreamStatus") == 429):
                    # Both transports share the relay's rate limit. A second
                    # immediate request would add load rather than recover.
                    break
            finally:
                # A valid store list remains useful even if all stock requests
                # fail. Reusing it must not extend its original cache lifetime.
                official_stores = getattr(adapter, 'selected_stores', None)
                if official_store_key and not used_official_store_cache and isinstance(official_stores, list):
                    with self._cache_lock:
                        self._store_cache[official_store_key] = (time.time(), deepcopy(official_stores))
        if not tracks:
            error = LiveDataError("재고 조회 경로에서 결과를 확인하지 못했습니다.", {"paths": failure_details})
            error.metrics = metrics
            raise error
        merged = merge_inventory_tracks(tracks)
        incomplete = incomplete or any(not row.stock_known for row in merged)
        unresolved.difference_update(row.product_code for row in merged if row.stock_known)
        issues = list(dict.fromkeys(coverage_notes))
        if unresolved:
            issues.append(f"응답 {len(unresolved)}건의 재고를 확인하지 못했습니다.")
        if incomplete:
            issues.append("응답에서 일부 정보를 확인하지 못했습니다.")
        if failures and not any(name == "보조" for name, _ in tracks):
            issues.append("보조 확인에도 실패해 확인한 범위만 표시합니다.")
        return merged, " ".join(issues), metrics, {"paths": failure_details} if failure_details else {}

    def _search_inventory_tracks(self, brand, keyword, user_lat, user_lng):
        return self._query_inventory(brand, keyword, user_lat, user_lng)[0]

    @staticmethod
    def _mark_track_observation(rows, track_name, completion_time=False):
        received = datetime.now().astimezone().isoformat(timespec="seconds")
        for row in rows:
            row.stock_source = row.stock_source or ("공개 재고 중계 서비스" + (" (보조)" if track_name == "보조" else ""))
            row.received_at = row.received_at or received
        return rows

    def _record_metric(self, brand, path, adapter, started, success, fallback, error=None):
        count = max(getattr(adapter, "request_count", 0), getattr(getattr(adapter, "mcp", None), "request_count", 0)) if adapter else 0
        inner = getattr(adapter, "inner_adapter", None)
        if inner is not None:
            count += max(getattr(inner, "request_count", 0), getattr(getattr(inner, "mcp", None), "request_count", 0))
        metric = {"brand": brand, "path": path, "request_count": count,
                  "elapsed_ms": round((time.perf_counter()-started)*1000, 2),
                  "success": success, "fallback_used": fallback,
                  "error_type": type(error).__name__ if error else ""}
        with self._metrics_lock:
            self.metrics.append(metric)
            self.metrics[:] = self.metrics[-200:]
        self.log(json.dumps(metric, ensure_ascii=False))
        return metric

    @staticmethod
    def _error_result(brand, error, prefix):
        details = _SANITIZE(getattr(error, "details", {}))
        metrics = getattr(error, "metrics", [])
        return DataLoadResult(brand, [], "ERROR", prefix + " 잠시 후 다시 시도해 주세요.", error_details=details,
                              request_count=sum(m["request_count"] for m in metrics),
                              elapsed_ms=sum(m["elapsed_ms"] for m in metrics) if metrics else None,
                              fallback_used=any(m["fallback_used"] for m in metrics))

    def load_all_test_records(self):
        return self.test_adapter.search("", 0, 0)

    @staticmethod
    def _mark_test(records):
        for row in records:
            row.data_source = "TEST"
            row.stock_source = row.stock_source or "시연 CSV"
            row.price_source = row.price_source or "시연 CSV"
            row.stock_data_status = "TEST"
            row.price_data_status = "TEST"
            row.promotion_data_status = "TEST"

    def _load_cache(self):
        try:
            for entry in json.loads(self.cache_path.read_text(encoding="utf-8")):
                if entry.get("search_version") != self.LIVE_CACHE_VERSION:
                    continue
                key = (entry["brand"], entry["keyword"], float(entry["lat"]), float(entry["lng"]),
                       str(entry.get("product_code", "")), str(entry.get("barcode", "")))
                self._last_good[key] = (float(entry["saved_at"]), [StoreProduct(**row) for row in entry["records"]])
                self._cache_issues[key] = str(entry.get("partial_message") or "")
        except (OSError, ValueError, TypeError, KeyError):
            self._last_good = {}

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        entries = [{"brand": key[0], "keyword": key[1], "lat": key[2], "lng": key[3],
                    "product_code": key[4], "barcode": key[5], "saved_at": saved_at,
                    "search_version": self.LIVE_CACHE_VERSION, "emart24_cache_version": self.EMART24_CACHE_VERSION,
                    "partial_message": self._cache_issues.get(key, ""), "records": [asdict(row) for row in rows]}
                   for key, (saved_at, rows) in self._last_good.items()]
        pending = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        pending.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        pending.replace(self.cache_path)
