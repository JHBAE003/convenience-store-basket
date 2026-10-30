from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import re
import time
from urllib.parse import urljoin

import requests

from core.models import StoreProduct


class _CuProducts(HTMLParser):
    """Extract product cards from CU's public plusAjax response."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.item: dict[str, str] | None = None
        self.capture = ""
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag == "li" and "prod_list" in classes:
            self.item = {"name": "", "price": "", "image": ""}
            self.items.append(self.item)
            self.depth = 1
        elif self.item is not None:
            if tag == "li":
                self.depth += 1
            if tag == "img" and a.get("src") and not self.item["image"]:
                self.item["image"] = a["src"]
            if "name" in classes:
                self.capture = "name"
            elif "price" in classes:
                self.capture = "price"

    def handle_data(self, data):
        if self.item is not None and self.capture:
            self.item[self.capture] += data.strip()

    def handle_endtag(self, tag):
        if tag == "li" and self.item is not None:
            self.depth -= 1
            if self.depth <= 0:
                self.item = None
                self.capture = ""
        elif tag in {"p", "strong", "div"}:
            self.capture = ""


class _SevenProducts(HTMLParser):
    """Extract SevenEleven product cards rendered in its public catalog."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.item: dict[str, str] | None = None
        self.capture = ""
        self.list_depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag == "div" and "pic_product" in classes:
            self.item = {"name": "", "price": "", "image": "", "code": ""}
            self.items.append(self.item)
        elif self.item is not None:
            if tag == "div" and "name" in classes:
                self.capture = "name"
            elif tag == "div" and "price" in classes:
                self.capture = "price"
            elif tag == "img" and a.get("src"):
                self.item["image"] = a["src"]
                alt = a.get("alt", "")
                if re.fullmatch(r"\d{8,14}", alt):
                    self.item["code"] = alt
        # SevenEleven's card/detail identifiers are not product barcodes. Do not
        # use them to join inventory; exact brand/product names are safer here.

    def handle_data(self, data):
        if self.item is not None and self.capture:
            self.item[self.capture] += data.strip()

    def handle_endtag(self, tag):
        if tag in {"div", "span", "strong"}:
            self.capture = ""
        if tag == "div" and self.item is not None and not self.capture:
            # A card ends at its outer pic_product div; HTMLParser does not expose
            # classes on end tags, so closure is finalized when the next card starts.
            pass


class CuPromotionScraper:
    BASE = "https://cu.bgfretail.com"
    PAGE = BASE + "/event/plus.do?category=event&depth2=1&sf=N"
    AJAX = BASE + "/event/plusAjax.do"

    def __init__(self, session: requests.Session | None = None, timeout: int = 12):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ConvenienceStoreCompare/1.0 (read-only)"})
        self.timeout = timeout

    def search(self, keyword: str) -> list[StoreProduct]:
        q = _normalise(keyword)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        result = {}
        for page, (depth, promo) in enumerate(((23, "1+1"), (24, "2+1"))):
            if page:
                time.sleep(0.15)
            self.session.get(self.PAGE, timeout=self.timeout).raise_for_status()
            for page_no in range(1, 31):
                if page_no > 1:
                    time.sleep(0.12)
                response = self.session.post(self.AJAX, data={
                    "pageIndex": str(page_no), "listType": "0" if page_no == 1 else "1",
                    "searchCondition": "", "category": "event", "depth2": str(depth), "sf": "N",
                }, headers={"Referer": self.PAGE, "X-Requested-With": "XMLHttpRequest"}, timeout=self.timeout)
                if response.status_code == 429:
                    raise RuntimeError("CU 행사 페이지 요청 제한(HTTP 429)")
                response.raise_for_status()
                html = response.content.decode("utf-8", "replace")
                parser = _CuProducts()
                parser.feed(html)
                if not parser.items:
                    break
                for card in parser.items:
                    name = card["name"].strip()
                    if not name or (q and q not in _normalise(name)):
                        continue
                    price = _price(card["price"])
                    if not price:
                        continue
                    image = urljoin(self.BASE, card["image"])
                    code_match = re.search(r"/(\d{8,14})\.(?:jpg|jpeg|png|webp)", image, re.I)
                    code = code_match.group(1) if code_match else ""
                    row = _promotion_row("CU", name, code, price, promo, image, now, "CU 공식 행사 페이지")
                    result[(code or _normalise(name), promo)] = row
                if "prodListBtn" not in html:
                    break
        return list(result.values())


class SevenElevenPromotionScraper:
    BASE = "https://www.7-eleven.co.kr"
    PAGE = BASE + "/product/presentList.asp"
    AJAX = BASE + "/product/listMoreAjax.asp"
    TABS = ((1, "1+1"), (2, "2+1"), (3, "덤증정"), (4, "할인"))
    MAX_PAGES_PER_TAB = 10

    def __init__(self, session: requests.Session | None = None, timeout: int = 12):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ConvenienceStoreCompare/1.0 (read-only)"})
        self.timeout = timeout

    def search(self, keyword: str) -> list[StoreProduct]:
        q = _normalise(keyword)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        result = {}
        for index, (tab, promo) in enumerate(self.TABS):
            if index:
                time.sleep(0.15)
            # The public page itself posts directly to this endpoint. A very
            # large intPageSize is capped by the server, so use its normal
            # paging size and avoid four redundant landing-page requests.
            for page_no in range(1, self.MAX_PAGES_PER_TAB + 1):
                if page_no > 1:
                    time.sleep(0.12)
                page = self.session.post(self.AJAX, data={
                    "intPageSize": "20", "intCurrPage": str(page_no),
                    "cateCd1": "", "cateCd2": "", "cateCd3": "", "pTab": str(tab),
                }, headers={"Referer": f"{self.PAGE}?pTab={tab}",
                            "X-Requested-With": "XMLHttpRequest"}, timeout=self.timeout)
                if page.status_code == 429:
                    raise RuntimeError("세븐일레븐 행사 페이지 요청 제한(HTTP 429)")
                page.raise_for_status()
                parser = _SevenProducts()
                parser.feed(page.content.decode("utf-8", "replace"))
                if not parser.items:
                    break
                for card in parser.items:
                    name = card["name"].strip()
                    if not name or (q and q not in _normalise(name)):
                        continue
                    price = _price(card["price"])
                    if not price:
                        continue
                    image = urljoin(self.BASE, card["image"])
                    code = card["code"]
                    row = _promotion_row("세븐일레븐", name, code, price, promo, image, now,
                                         "세븐일레븐 공식 행사 페이지")
                    result[(code or _normalise(name), promo)] = row
        return list(result.values())


def _promotion_row(brand, name, code, price, promo, image, now, source):
    return StoreProduct(brand=brand, store_name="", address="", product_name=name,
                        barcode=code if re.fullmatch(r"\d{8,14}", code or "") else "",
                        product_code=code or "", price=price, promotion=promo,
                        promotion_known=True, stock=0, stock_known=False,
                        data_source="LIVE", updated_at=now, price_updated_at=now,
                        price_source=source, promotion_source=source,
                        price_data_status="LIVE", promotion_data_status="LIVE", image_url=image)


def _normalise(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", value.casefold())


def _price(value: str) -> int:
    digits = re.sub(r"[^0-9]", "", value or "")
    return int(digits) if digits else 0
