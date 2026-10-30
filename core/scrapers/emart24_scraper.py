from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests

from core.models import StoreProduct


class _ProductCardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self.card: dict[str, Any] | None = None
        self.card_depth = 0
        self.stack: list[tuple[str, str]] = []
        self.capture: str | None = None
        self.buffer = ""

    def handle_starttag(self, tag: str, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "")
        if tag == "div" and "itemWrap" in classes.split():
            self.card = {"name": "", "prices": [], "labels": [], "images": []}
            self.cards.append(self.card)
            self.card_depth = 1
            self.stack = [(tag, classes)]
            return
        if self.card is None:
            return
        if tag == "div":
            self.card_depth += 1
        self.stack.append((tag, classes))
        if tag == "img" and attributes.get("src"):
            self.card["images"].append(attributes["src"])
        if tag == "span" and any(label in classes.split() for label in ("sale", "gola", "event")):
            self.capture = "label"
            self.buffer = ""
        elif tag == "a" and "price" in classes.split():
            self.capture = "price"
            self.buffer = ""
        elif tag == "a" and any(parent_class == "itemtitle" for _, parent_class in self.stack[:-1]):
            self.capture = "name"
            self.buffer = ""

    def handle_data(self, data: str):
        if self.card is None:
            return
        value = data.strip()
        if not value:
            return
        for tag, classes in self.stack:
            if tag == "span" and any(label in classes.split() for label in ("sale", "gola", "event")):
                self.card["labels"].append(value)
        if self.capture:
            self.buffer += value

    def handle_endtag(self, tag: str):
        if self.card is None:
            return
        if self.capture and self.stack and self.stack[-1][0] == tag:
            value = self.buffer.strip()
            if self.capture == "name" and value:
                self.card["name"] = value
            elif self.capture == "price" and value:
                self.card["prices"].append(value)
            self.capture = None
            self.buffer = ""
        if tag == "div":
            self.card_depth -= 1
            if self.card_depth <= 0:
                self.card = None
                self.stack = []
                return
        if self.stack:
            self.stack.pop()


class Emart24PromotionScraper:
    URL = "https://emart24.co.kr/goods/event"

    def __init__(self, session: requests.Session | None = None, timeout: int = 12):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ConvenienceStoreCompare/1.0 (read-only)", "Accept": "text/html"})
        self.timeout = timeout

    def search(self, keyword: str) -> list[StoreProduct]:
        query = self._normalize(keyword)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        result: dict[tuple[str, str], StoreProduct] = {}
        # These category IDs are read from the public links rendered by the
        # official page: 1+1, 2+1, 3+1, sale, and mix-and-match.
        categories = ((1, "1+1"), (2, "2+1"), (3, "3+1"), (4, "세일"), (12, "골라담기"))
        for index, (category, category_label) in enumerate(categories):
            if index:
                time.sleep(0.2)
            response = self.session.get(self.URL, params={
                "search": keyword.strip(), "category_seq": category,
                "base_category_seq": "", "align": "",
            }, timeout=self.timeout)
            if response.status_code == 429:
                raise RuntimeError("이마트24 행사 페이지 요청 제한(HTTP 429)")
            response.raise_for_status()
            parser = _ProductCardParser()
            parser.feed(response.text)
            for card in parser.cards:
                name = str(card["name"]).strip()
                if not name or (query and query not in self._normalize(name)):
                    continue
                image = urljoin(self.URL, card["images"][0]) if card["images"] else ""
                code_match = re.search(r"/(\d{8,14})\.(?:jpg|jpeg|png|webp)(?:\?|$)", image, re.I)
                code = code_match.group(1) if code_match else ""
                prices = [self._parse_price(x) for x in card["prices"]]
                prices = [price for price in prices if price is not None]
                if not prices:
                    continue
                labels = list(dict.fromkeys(str(x).strip() for x in card["labels"] if str(x).strip()))
                promotion = labels[-1] if labels else category_label
                current_price = prices[-1] if len(prices) > 1 else prices[0]
                record = StoreProduct(
                    brand="이마트24", store_name="", address="", product_name=name,
                    barcode=code, product_code=code, price=current_price, promotion=promotion,
                    promotion_known=True, stock=0, stock_known=False,
                    data_source="LIVE", updated_at=timestamp, price_updated_at=timestamp,
                    price_source="이마트24 공식 행사 페이지", promotion_source="이마트24 공식 행사 페이지",
                    price_data_status="LIVE", promotion_data_status="LIVE", image_url=image,
                )
                result[(code or self._normalize(name), promotion)] = record
        return list(result.values())

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]", "", value.casefold())

    @staticmethod
    def _parse_price(value: str) -> int | None:
        digits = re.sub(r"[^0-9]", "", value)
        return int(digits) if digits else None
