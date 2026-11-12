from __future__ import annotations

from dataclasses import asdict, replace
from concurrent.futures import Future
import json
from pathlib import Path
import time
import threading
import re

from core.config import BASE_DIR, PROMOTION_CACHE_TTL_SECONDS
from core.models import StoreProduct
from core.services.promotion import promotion_applicability
from core.scrapers.emart24_scraper import Emart24PromotionScraper
from core.scrapers.public_event_scraper import CuPromotionScraper, SevenElevenPromotionScraper
from core.scrapers.gs25_price_scraper import Gs25PriceScraper


class PromotionManager:
    """Brand-level promotion lookup with a separate persistent cache."""

    CATALOG_BRANDS = {"CU", "세븐일레븐"}

    def __init__(self, cache_path: str | Path | None = None, scraper=None,
                 ttl_seconds: int = PROMOTION_CACHE_TTL_SECONDS, *, defer_catalog: bool = False):
        self.cache_path = Path(cache_path) if cache_path else BASE_DIR / ".runtime" / "promotions_cache.json"
        self.defer_catalog = defer_catalog
        self.scrapers = scraper or {
            "이마트24": Emart24PromotionScraper(),
            "CU": CuPromotionScraper(max_pages=2 if defer_catalog else None),
            "세븐일레븐": SevenElevenPromotionScraper(max_pages=1 if defer_catalog else None),
            "GS25": Gs25PriceScraper(),
        }
        self.ttl_seconds = ttl_seconds
        self.cache = self._load()
        self._cache_lock = threading.RLock()
        self._inflight: dict[str, Future] = {}
        self._failure_until: dict[str, float] = {}
        self._issues: dict[tuple[str, str], dict] = {}

    def search(self, brand: str, keyword: str, force: bool = False, *,
               allow_catalog_fetch: bool = False) -> list[StoreProduct]:
        if not keyword.strip():
            return []
        scraper = self.scrapers.get(brand)
        if scraper is None:
            return []
        catalog = brand in self.CATALOG_BRANDS
        key = f"{brand}|*" if catalog else f"{brand}|{keyword.casefold().strip()}"
        legacy_key = f"{brand}|{keyword.casefold().strip()}"
        with self._cache_lock:
            saved = self.cache.get(key)
            legacy = self.cache.get(legacy_key) if catalog and saved is None else None
        now = time.time()
        query_key = (brand, keyword.casefold().strip())
        deferred = self.defer_catalog and catalog and not allow_catalog_fetch
        use_cache = not force or deferred
        ttl = self.ttl_seconds if saved and saved.get("records") else min(60, self.ttl_seconds)
        if use_cache and saved and 0 <= now - saved["saved_at"] <= ttl:
            partial = self._is_partial(saved.get('records', []), saved=saved)
            self._set_issue(query_key, "PARTIAL" if partial else ("CACHE" if saved.get("records") else "UNKNOWN"),
                            "저장된 행사 목록 일부를 표시합니다." if partial else
                            ("저장된 행사 자료" if saved.get("records") else "행사 수집 결과 없음: 행사 여부 미확인"))
            return self._match_keyword(self._restore(saved), keyword)
        if use_cache and legacy and 0 <= now - legacy["saved_at"] <= self.ttl_seconds:
            partial = self._is_partial(legacy.get('records', []), saved=legacy)
            self._set_issue(query_key, "PARTIAL" if partial else "CACHE",
                            "저장된 행사 목록 일부를 표시합니다." if partial else "저장된 행사 자료")
            return self._restore(legacy)
        if deferred:
            self._set_issue(query_key, "UNKNOWN", "행사 목록은 추가 확인 버튼으로 조회할 수 있습니다.")
            return []
        with self._cache_lock:
            if self._failure_until.get(key, 0) > time.monotonic():
                self._set_issue(query_key, "ERROR", "행사 조회가 불안정하여 잠시 기다린 뒤 다시 확인합니다.")
                if saved:
                    return self._match_keyword(self._restore(saved), keyword)
                raise RuntimeError("행사 조회가 불안정합니다. 잠시 뒤 다시 확인하세요.")
            flight = self._inflight.get(key)
            owner = flight is None
            if owner:
                flight = Future()
                self._inflight[key] = flight
        if not owner:
            try:
                records = flight.result()
            except Exception:
                self._set_issue(query_key, "ERROR", "행사 조회 실패: 행사 여부 미확인")
                if saved:
                    return self._match_keyword(self._restore(saved), keyword)
                raise
            with self._cache_lock:
                shared_saved = self.cache.get(key)
            partial = self._is_partial(records, saved=shared_saved)
            self._set_issue(query_key, "PARTIAL" if partial else ("LIVE" if records else "UNKNOWN"),
                            "행사 목록 일부만 확인했습니다." if partial else
                            ("진행 중인 행사 조회를 함께 사용했습니다." if records else "행사 수집 결과 없음: 행사 여부 미확인"))
            return self._match_keyword([replace(row, raw_payload=dict(row.raw_payload)) for row in records], keyword)
        try:
            records = scraper.search("" if catalog else keyword)
            for row in records:
                # Public brand catalogs never prove the current price at one store.
                row.price_data_status = "REFERENCE" if row.price else "UNKNOWN"
                allowed, reason = promotion_applicability(row)
                if not allowed:
                    row.raw_payload = {**row.raw_payload, "calculation_note": reason}
            partial = self._is_partial(records, scraper=scraper)
            self._set_issue(query_key, "PARTIAL" if partial else ("LIVE" if records else "UNKNOWN"),
                            "행사 목록 일부만 확인했습니다." if partial else
                            ("행사 목록 확인" if records else "행사 수집 결과 없음: 행사 여부 미확인"))
        except Exception as exc:
            with self._cache_lock:
                self._failure_until[key] = time.monotonic() + 15
                flight.set_exception(exc)
                self._inflight.pop(key, None)
            self._set_issue(query_key, "ERROR", "행사 조회 실패: 행사 여부 미확인")
            if saved:
                return self._match_keyword(self._restore(saved), keyword)
            if legacy:
                return self._restore(legacy)
            raise
        with self._cache_lock:
            self.cache[key] = {"saved_at": now, "records": [asdict(record) for record in records], "partial": partial}
            try:
                self._save()
            except OSError:
                pass
            flight.set_result(records)
            self._inflight.pop(key, None)
        return self._match_keyword(records, keyword)

    def _set_issue(self, key, status, message):
        with self._cache_lock:
            self._issues[key] = {"status": status, "message": message}

    @staticmethod
    def _is_partial(records, *, scraper=None, saved=None) -> bool:
        return bool((saved and saved.get('partial'))
                    or (scraper is not None and getattr(scraper, 'last_collection_complete', True) is False)
                    or any((row.get('raw_payload', {}) if isinstance(row, dict) else row.raw_payload)
                           .get('collection_complete') is False for row in records))

    def status_for(self, brand: str, keyword: str) -> dict:
        with self._cache_lock:
            return dict(self._issues.get((brand, keyword.casefold().strip()),
                                        {"status": "UNKNOWN", "message": "행사 여부 미확인"}))

    @staticmethod
    def _restore(saved: dict) -> list[StoreProduct]:
        rows = [StoreProduct(**item) for item in saved["records"]]
        return [replace(row, data_source="CACHE", app_cache_used=True,
                        price_data_status="REFERENCE" if row.price else "UNKNOWN",
                        promotion_known=row.promotion_known and promotion_applicability(row)[0],
                        promotion_data_status="CACHE" if row.promotion else "UNKNOWN")
                for row in rows]

    @staticmethod
    def _match_keyword(records: list[StoreProduct], keyword: str) -> list[StoreProduct]:
        key = re.sub(r"[^0-9a-z가-힣]", "", keyword.casefold())
        if not key:
            return []
        return [row for row in records if
                key in re.sub(r"[^0-9a-z가-힣]", "", row.product_name.casefold())
                or key in (row.barcode, row.product_code)]

    def _load(self) -> dict:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.cache_path)
