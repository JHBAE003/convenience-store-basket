from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote

import requests

from core.models import StoreProduct


class _StructuredData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._parts = []

    def handle_data(self, data):
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._parts is not None:
            self.blocks.append("".join(self._parts))
            self._parts = None


class Gs25PriceScraper:
    """Targeted lookup of publicly listed GS25 promotion prices.

    This is a third-party event catalog, not a GS25 inventory endpoint. A
    catalog price is usable only when its image contains a product barcode.
    """

    SEARCH_URL = "https://xn--vf4b15j1pa468argc.com/"
    SOURCE = "편의점행사.com GS25 행사 목록"

    def __init__(self, session: requests.Session | None = None, timeout: int = 10):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ConvenienceStoreCompare/1.0 (read-only)"})
        self.timeout = timeout

    def search(self, keyword: str) -> list[StoreProduct]:
        if not keyword.strip():
            return []
        response = self.session.post(self.SEARCH_URL, data={
            "item_shop": "GS25", "serche_text": keyword.strip(),
        }, timeout=self.timeout)
        response.raise_for_status()
        parser = _StructuredData()
        parser.feed(response.text)
        now = datetime.now().astimezone()
        month = f"{now.year}년 {now.month}월"
        observed = now.isoformat(timespec="seconds")
        results: dict[str, StoreProduct] = {}
        for block in parser.blocks:
            try:
                document = json.loads(block)
            except (TypeError, ValueError):
                continue
            if document.get("@type") != "ItemList":
                continue
            for entry in document.get("itemListElement", []):
                item = entry.get("item", {})
                if not isinstance(item, dict) or item.get("@type") != "Product":
                    continue
                description = str(item.get("description") or "")
                if month not in description or "GS25" not in description:
                    continue
                image = unquote(str(item.get("image") or ""))
                code_match = re.search(r"(?:^|/)GD_(\d{8,14})(?:_|\.)", image)
                if not code_match:
                    continue
                code = code_match.group(1)
                offer = item.get("offers") or {}
                if not isinstance(offer, dict) or offer.get("priceCurrency") != "KRW":
                    continue
                try:
                    price = int(str(offer.get("price", "")).replace(",", ""))
                except ValueError:
                    continue
                if price <= 0:
                    continue
                valid_until = str(offer.get("priceValidUntil") or "")
                if valid_until and valid_until < now.date().isoformat():
                    continue
                promo_match = re.search(r"(?<!\d)([123]\+1)(?!\d)", description)
                promotion = promo_match.group(1) if promo_match else ""
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                results[code] = StoreProduct(
                    brand="GS25", store_name="", address="", product_name=name,
                    barcode=code, product_code=code, price=price,
                    promotion=promotion, promotion_known=bool(promotion),
                    stock=0, stock_known=False, data_source="LIVE",
                    updated_at=observed, price_updated_at=observed,
                    price_source=self.SOURCE, promotion_source=self.SOURCE if promotion else "",
                    price_data_status="LIVE", promotion_data_status="LIVE" if promotion else "UNKNOWN",
                    image_url=str(item.get("image") or ""),
                )
        return list(results.values())
