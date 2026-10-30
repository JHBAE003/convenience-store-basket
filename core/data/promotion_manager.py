from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import time
import threading
import re

from core.config import BASE_DIR
from core.models import StoreProduct
from core.scrapers.emart24_scraper import Emart24PromotionScraper
from core.scrapers.public_event_scraper import CuPromotionScraper, SevenElevenPromotionScraper
from core.scrapers.gs25_price_scraper import Gs25PriceScraper


class PromotionManager:
    """Brand-level promotion lookup with a separate persistent cache."""

    CATALOG_BRANDS = {"CU", "세븐일레븐"}

    def __init__(self, cache_path: str | Path | None = None, scraper=None,
                 ttl_seconds: int = 6 * 60 * 60):
        self.cache_path = Path(cache_path) if cache_path else BASE_DIR / ".runtime" / "promotions_cache.json"
        self.scrapers = scraper or {
            "이마트24": Emart24PromotionScraper(),
            "CU": CuPromotionScraper(),
            "세븐일레븐": SevenElevenPromotionScraper(),
            "GS25": Gs25PriceScraper(),
        }
        self.ttl_seconds = ttl_seconds
        self.cache = self._load()
        self._cache_lock = threading.RLock()

    def search(self, brand: str, keyword: str) -> list[StoreProduct]:
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
        if saved and 0 <= now - saved["saved_at"] <= self.ttl_seconds:
            return self._match_keyword(self._restore(saved), keyword)
        if legacy and 0 <= now - legacy["saved_at"] <= self.ttl_seconds:
            return self._restore(legacy)
        try:
            records = scraper.search("" if catalog else keyword)
        except Exception:
            if saved:
                return self._match_keyword(self._restore(saved), keyword)
            if legacy:
                return self._restore(legacy)
            raise
        with self._cache_lock:
            self.cache[key] = {"saved_at": now, "records": [asdict(record) for record in records]}
            try:
                self._save()
            except OSError:
                pass
        return self._match_keyword(records, keyword)

    @staticmethod
    def _restore(saved: dict) -> list[StoreProduct]:
        rows = [StoreProduct(**item) for item in saved["records"]]
        return [replace(row, data_source="CACHE",
                        price_data_status="CACHE" if row.price else "UNKNOWN",
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
