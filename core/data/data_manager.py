from __future__ import annotations

from pathlib import Path
from typing import Callable
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time
import json
from dataclasses import asdict
import threading

from core.config import BRANDS, LIVE_CACHE_TTL_SECONDS
from core.data.csv_adapter import CsvAdapter
from core.data.composite_inventory_adapter import CompositeInventoryAdapter, merge_inventory_tracks
from core.data.live_mcp_adapter import DaisoMcpAdapter
from core.data.promotion_manager import PromotionManager
from core.models import DataLoadResult, StoreProduct
from core.services.integration_service import integrate_records


class DataManager:
    LIVE_CACHE_VERSION = 3
    EMART24_CACHE_VERSION = 1
    VALID_MODES = {"TEST", "LIVE"}

    def __init__(self, test_csv: str | Path, mode: str = "TEST", log: Callable[[str], None] | None = None,
                 cache_path: str | Path | None = None, cache_ttl_seconds: int = LIVE_CACHE_TTL_SECONDS,
                 promotion_manager: PromotionManager | None = None):
        self.test_csv = Path(test_csv)
        self.mode = mode.upper()
        if self.mode not in self.VALID_MODES:
            raise ValueError(f"지원하지 않는 데이터 모드: {mode}")
        self.log = log or (lambda message: None)
        self.test_adapter = CsvAdapter(self.test_csv)
        self.promotion_manager = promotion_manager if promotion_manager is not None else PromotionManager()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._promotion_errors: dict[str, str] = {}
        self.cache_path = Path(cache_path) if cache_path else self.test_csv.with_name("live_cache.json")
        self._last_good: dict[tuple[str, str, float, float], tuple[float, list[StoreProduct]]] = {}
        self._cache_issues: dict[tuple[str, str, float, float], str] = {}
        self._inventory_issues: dict[str, str] = {}
        self._search_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._load_cache()

    def search_all(self, keyword: str, user_lat: float, user_lng: float, brands: list[str] | None = None,
                   mode: str | None = None, *, include_unpriced: bool = False,
                   force_refresh: bool = False) -> tuple[list[StoreProduct], list[DataLoadResult]]:
        with self._search_lock:
            return self._search_all_locked(keyword, user_lat, user_lng, brands, mode, include_unpriced, force_refresh)

    def _search_all_locked(self, keyword: str, user_lat: float, user_lng: float, brands: list[str] | None = None,
                           mode: str | None = None, include_unpriced: bool = False,
                           force_refresh: bool = False) -> tuple[list[StoreProduct], list[DataLoadResult]]:
        brands = BRANDS if brands is None else brands
        active_mode = (mode or self.mode).upper()
        if active_mode not in self.VALID_MODES:
            raise ValueError(f"지원하지 않는 데이터 모드: {active_mode}")
        all_records: list[StoreProduct] = []
        statuses: list[DataLoadResult] = []

        if active_mode == "LIVE" and len(brands) > 1:
            # Remote brands can wait independently; map keeps the UI's brand order.
            with ThreadPoolExecutor(max_workers=min(4, len(brands))) as pool:
                results = list(pool.map(
                    lambda brand: self._search_brand(brand, keyword, user_lat, user_lng, active_mode, force_refresh),
                    brands,
                ))
        else:
            results = [self._search_brand(brand, keyword, user_lat, user_lng, active_mode, force_refresh) for brand in brands]
        for result in results:
            priced = [row for row in result.records if row.has_store_identity and (include_unpriced or row.price > 0)]
            missing_price = sum(1 for row in result.records if row.has_store_identity and row.price <= 0)
            message = result.message
            if missing_price and not include_unpriced:
                message += f" 가격 미확인 상품 {missing_price}건을 결과에서 제외했습니다."
            statuses.append(replace(result, records=priced, message=message))
            all_records.extend(priced)

        # Keep unpriced rows in the cache for later enrichment, not in results.
        return all_records, statuses

    def _search_brand(self, brand: str, keyword: str, user_lat: float, user_lng: float,
                      mode: str | None = None, force_refresh: bool = False) -> DataLoadResult:
        active_mode = (mode or self.mode).upper()
        if active_mode == "LIVE":
            cache_key = self._cache_key(brand, keyword, user_lat, user_lng)
            cached = self._last_good.get(cache_key)
            if cached and not force_refresh:
                saved_at, saved = cached
                age = time.time() - saved_at
                if 0 <= age <= self.cache_ttl_seconds and saved:
                    rows = [replace(row) for row in saved if row.has_store_identity]
                    if rows:
                        for row in rows:
                            if not row.price:
                                row.price_data_status = "UNKNOWN"
                                row.price_source = ""
                                row.price_updated_at = ""
                        if brand == "GS25" and any(not row.price for row in rows):
                            prices = self._get_promotion_records(brand, keyword, rows)
                            if prices:
                                rows = integrate_records(rows, prices)
                                with self._cache_lock:
                                    self._last_good[cache_key] = (saved_at, [replace(row) for row in rows])
                                    try:
                                        self._save_cache()
                                    except OSError as exc:
                                        self.log(f"{brand}: 캐시 저장 실패: {exc}")
                        self.log(f"{brand}: LIVE 저장 결과 {len(rows)}건 ({int(age / 60)}분 전)")
                        issue = self._cache_issues.get(cache_key, "")
                        return DataLoadResult(
                            brand, rows, "PARTIAL" if issue else "LIVE",
                            (issue + " " if issue else "") + f"{self.cache_ttl_seconds}초 이내 저장 결과를 사용해 외부 조회를 생략했습니다.", age
                        )
            try:
                if brand in BRANDS:
                    records = self._search_inventory_tracks(brand, keyword, user_lat, user_lng)
                else:
                    records = DaisoMcpAdapter(brand).search(keyword, user_lat, user_lng)
                retrieved_at = time.time()
                # Event catalog rows cannot establish which nearby store has stock.
                records = [row for row in records if row.has_store_identity]
                promotions = self._get_promotion_records(brand, keyword, records) if records else []
                if records and promotions:
                    records = integrate_records(records, promotions)
                if brand in self._promotion_errors:
                    for row in records:
                        if not row.promotion:
                            row.promotion_known = False
                            row.promotion_data_status = "ERROR"
                if not records:
                    return DataLoadResult(brand, [], "LIVE", "API에 연결했지만 점포 재고 결과가 없습니다.")
                for row in records:
                    if row.price:
                        row.price_source = row.price_source or "Daiso MCP"
                    row.data_source = "LIVE"
                    row.stock_source = row.stock_source or "Daiso MCP"
                    row.stock_data_status = "LIVE"
                    if row.price and row.price_data_status == "UNKNOWN":
                        row.price_data_status = "LIVE"
                    row.promotion_data_status = row.promotion_data_status if row.promotion_data_status != "UNKNOWN" else ("LIVE" if row.promotion else "UNKNOWN")
                    if not row.promotion and row.promotion_data_status == "UNKNOWN":
                        row.promotion_known = False
                    row.stock_updated_at = row.stock_updated_at or row.updated_at
                    if row.price:
                        row.price_updated_at = row.price_updated_at or row.updated_at
                issue = self._inventory_issues.get(brand, "")
                with self._cache_lock:
                    self._last_good[cache_key] = (retrieved_at, [replace(row) for row in records])
                    if issue:
                        self._cache_issues[cache_key] = issue
                    else:
                        self._cache_issues.pop(cache_key, None)
                    try:
                        self._save_cache()
                    except OSError as exc:
                        self.log(f"{brand}: 캐시 저장 실패: {exc}")
                self.log(f"{brand}: LIVE {len(records)}건")
                message = "두 조회 경로의 점포별 결과를 비교해 최신 재고를 선택했습니다."
                if issue:
                    message += " " + issue
                return DataLoadResult(brand, records, "PARTIAL" if issue else "LIVE", message)
            except Exception as exc:
                message = f"실시간 조회 실패: {exc}"
                self.log(f"{brand}: {message}")
                if cached:
                    age, saved = cached
                    rows = [replace(row, data_source="CACHE",
                                    price_data_status="CACHE" if row.price else "UNKNOWN",
                                    stock_data_status="CACHE",
                                    promotion_data_status="CACHE" if row.promotion else "UNKNOWN") for row in saved]
                    for row in rows:
                        row.stock_data_status = "CACHE"
                    self.log(f"{brand}: CACHE {len(rows)}건")
                    return DataLoadResult(brand, rows, "CACHE", message + " 이전 조회 캐시를 표시합니다.", max(0, time.time() - age))
                return DataLoadResult(brand, [], "ERROR", message)

        test_records = [x for x in self.test_adapter.search(keyword, user_lat, user_lng) if x.brand == brand]
        self._mark_test(test_records)
        self.log(f"{brand}: TEST {len(test_records)}건")
        return DataLoadResult(brand, test_records, "TEST", "테스트 데이터 사용")

    def _search_inventory_tracks(self, brand: str, keyword: str,
                                 user_lat: float, user_lng: float) -> list[StoreProduct]:
        """Run both supported inventory paths independently for every brand."""
        emart_region = None
        if brand == "이마트24":
            from core.services.geocoding import emart24_region_korean
            emart_region = emart24_region_korean(user_lat, user_lng)

        def existing_track():
            adapter = (DaisoMcpAdapter(brand, emart_region=emart_region)
                       if emart_region else DaisoMcpAdapter(brand))
            rows = self._mark_track_observation(
                adapter.search(keyword, user_lat, user_lng), "Track 1", completion_time=True,
            )
            return rows, getattr(adapter, "lookup_failures", []), getattr(adapter, "catalog_incomplete", False)

        def composite_track():
            adapter = (CompositeInventoryAdapter(brand, emart_region=emart_region)
                       if emart_region else CompositeInventoryAdapter(brand))
            rows = self._mark_track_observation(
                adapter.search(keyword, user_lat, user_lng), "Track 2", completion_time=True,
            )
            return rows, getattr(adapter, "lookup_failures", []), getattr(adapter, "catalog_incomplete", False)

        track_functions = (("Track 1", existing_track), ("Track 2", composite_track))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [(name, pool.submit(function)) for name, function in track_functions]
            track_results = []
            errors = []
            failed_codes = set()
            incomplete_catalog = False
            for name, future in futures:
                try:
                    rows, lookup_failures, catalog_incomplete = future.result()
                    failed_codes.update(code for code in lookup_failures if code)
                    incomplete_catalog = incomplete_catalog or catalog_incomplete
                    for row in rows:
                        if "Track 2" not in row.stock_source:
                            row.stock_source = f"Daiso MCP ({name})"
                        row.stock_updated_at = row.stock_updated_at or row.updated_at
                    track_results.append((name, rows))
                    self.log(f"{brand} {name}: {len(rows)}건")
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    incomplete_catalog = True
                    self.log(f"{brand} {name} 재고 조회 실패: {exc}")
        if not track_results:
            raise RuntimeError(" | ".join(errors) or "두 재고 조회 경로가 결과를 반환하지 않았습니다.")
        if errors:
            self.log(f"{brand}: 한 조회 경로 오류, 성공한 경로 결과를 사용합니다.")
        merged = merge_inventory_tracks(track_results)
        unresolved = failed_codes - {row.product_code for row in merged}
        issue_parts = []
        if unresolved:
            issue_parts.append(f"상품 {len(unresolved)}종의 재고 조회 실패")
        if incomplete_catalog:
            issue_parts.append("상품 목록 또는 조회 경로 일부 미제공")
        if issue_parts:
            self._inventory_issues[brand] = ", ".join(issue_parts) + "."
        else:
            self._inventory_issues.pop(brand, None)
        self.log(f"{brand}: 두 조회 경로에서 {sum(len(rows) for _, rows in track_results)}건 중 "
                 f"최신 점포 결과 {len(merged)}건 선택")
        return merged

    @staticmethod
    def _mark_track_observation(rows: list[StoreProduct], track_name: str,
                                completion_time: bool = False) -> list[StoreProduct]:
        observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for row in rows:
            if "Track 2" not in row.stock_source and track_name == "Track 2":
                row.stock_source = f"Daiso MCP ({track_name})"
            elif track_name == "Track 1" and "Track 1" not in row.stock_source:
                row.stock_source = f"Daiso MCP ({track_name})"
            # Most upstream responses do not expose their own stock timestamp.
            # In that case use the time the full request completed, consistently
            # across both tracks, instead of comparing their start times.
            if completion_time and row.stock_updated_at == row.updated_at:
                row.stock_updated_at = observed_at
        return rows

    def _get_promotion_records(self, brand: str, keyword: str,
                               inventory: list[StoreProduct] | None = None) -> list[StoreProduct]:
        self._promotion_errors.pop(brand, None)
        try:
            found = self.promotion_manager.search(brand, keyword)
            if brand != "GS25" or not inventory:
                return found
            # A broad user keyword may return only one page of catalog results.
            # Search remaining inventory products by their full name, with a
            # strict request cap. The manager caches each query for six hours.
            known_codes = {row.barcode or row.product_code for row in found}
            queries = []
            seen_codes = set()
            for row in inventory:
                code = row.barcode or row.product_code
                if not code or code in known_codes or code in seen_codes:
                    continue
                seen_codes.add(code)
                name = row.product_name.rsplit(")", 1)[-1].strip()
                if name and name.casefold() != keyword.casefold():
                    queries.append(name)
                if len(queries) >= 6:
                    break
            if queries:
                with ThreadPoolExecutor(max_workers=min(3, len(queries))) as pool:
                    for rows in pool.map(lambda q: self.promotion_manager.search(brand, q), queries):
                        found.extend(rows)
            return found
        except Exception as exc:
            self._promotion_errors[brand] = str(exc)
            self.log(f"{brand}: 행사·가격 자료 조회 실패: {exc}")
            return []

    def load_all_test_records(self) -> list[StoreProduct]:
        return self.test_adapter.search("", 0, 0)

    @staticmethod
    def _mark_test(records: list[StoreProduct]) -> None:
        for row in records:
            row.data_source = "TEST"
            row.stock_source = row.stock_source or "테스트 CSV"
            row.price_source = row.price_source or "테스트 CSV"
            row.stock_data_status = "TEST"
            row.price_data_status = "TEST"
            row.promotion_data_status = "TEST"

    @staticmethod
    def _cache_key(brand: str, keyword: str, lat: float, lng: float) -> tuple[str, str, float, float]:
        return brand, keyword.casefold().strip(), round(lat, 4), round(lng, 4)

    def _load_cache(self) -> None:
        try:
            document = json.loads(self.cache_path.read_text(encoding="utf-8"))
            for entry in document:
                if entry.get("search_version") != self.LIVE_CACHE_VERSION:
                    continue
                if entry.get("brand") == "이마트24" and entry.get("emart24_cache_version") != self.EMART24_CACHE_VERSION:
                    continue
                rows = [StoreProduct(**item) for item in entry["records"]]
                key = (entry["brand"], entry["keyword"], entry["lat"], entry["lng"])
                self._last_good[key] = (float(entry["saved_at"]), rows)
                if entry.get("partial_message"):
                    self._cache_issues[key] = str(entry["partial_message"])
        except (OSError, ValueError, TypeError, KeyError):
            self._last_good = {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        for (brand, keyword, lat, lng), (saved_at, rows) in self._last_good.items():
            entries.append({"brand": brand, "keyword": keyword, "lat": lat, "lng": lng,
                            "saved_at": saved_at, "search_version": self.LIVE_CACHE_VERSION,
                            "emart24_cache_version": self.EMART24_CACHE_VERSION if brand == "이마트24" else None,
                            "partial_message": self._cache_issues.get((brand, keyword, lat, lng), ""),
                            "records": [asdict(row) for row in rows]})
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.cache_path)
