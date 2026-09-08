from __future__ import annotations

import json
import re
from bs4 import BeautifulSoup, Tag

from models import Listing
from utils import absolute, clean_text, parse_int, stable_id
from .base import Scraper


class MobileDeScraper(Scraper):
    source = "mobile_de"

    def parse(self, html: str, search_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        items = self._json_ld(soup, search_url)
        if items:
            return items
        return self._html_fallback(soup, search_url)

    def _json_ld(self, soup: BeautifulSoup, search_url: str) -> list[Listing]:
        out: list[Listing] = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            stack = data if isinstance(data, list) else [data]
            for obj in stack:
                if not isinstance(obj, dict):
                    continue
                candidates = obj.get("itemListElement", []) if obj.get("@type") == "ItemList" else []
                for elem in candidates:
                    item = elem.get("item", elem) if isinstance(elem, dict) else {}
                    if not isinstance(item, dict):
                        continue
                    url = item.get("url")
                    title = clean_text(item.get("name"))
                    if not url or not title:
                        continue
                    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
                    price = offers.get("price")
                    out.append(Listing(
                        source=self.source,
                        external_id=stable_id(self.source, absolute(search_url, url)),
                        url=absolute(search_url, url),
                        title=title,
                        price=parse_int(str(price)) if price is not None else None,
                        currency=offers.get("priceCurrency") or "EUR",
                        image_url=(item.get("image") if isinstance(item.get("image"), str) else None),
                    ))
        return self._dedupe(out)

    def _html_fallback(self, soup: BeautifulSoup, search_url: str) -> list[Listing]:
        out: list[Listing] = []
        # mobile.de commonly exposes vehicle detail links containing /fahrzeuge/details.html
        for link in soup.select('a[href*="/fahrzeuge/details.html"], a[href*="/auto-inserat/"]'):
            href = link.get("href")
            if not href:
                continue
            url = absolute(search_url, href)
            card = self._card(link)
            text = clean_text(card.get_text(" ", strip=True))
            title = clean_text(link.get_text(" ", strip=True))
            if len(title) < 4:
                heading = card.find(["h2", "h3"])
                title = clean_text(heading.get_text(" ", strip=True)) if heading else ""
            if not title:
                continue

            price_match = re.search(r"€\s*([\d.,\s]+)|([\d.,\s]+)\s*€", text)
            price_raw = next((g for g in price_match.groups() if g), None) if price_match else None
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
            km_match = re.search(r"([\d.\s]+)\s*km\b", text, re.I)

            out.append(Listing(
                source=self.source,
                external_id=stable_id(self.source, url),
                url=url,
                title=title[:220],
                price=parse_int(price_raw),
                currency="EUR" if price_match else None,
                year=int(year_match.group(1)) if year_match else None,
                mileage_km=parse_int(km_match.group(1)) if km_match else None,
            ))
        return self._dedupe(out)

    @staticmethod
    def _card(link: Tag) -> Tag:
        node = link
        for _ in range(8):
            parent = node.parent
            if not isinstance(parent, Tag):
                break
            node = parent
            txt = clean_text(node.get_text(" ", strip=True))
            if "€" in txt and "km" in txt.lower():
                return node
        return link

    @staticmethod
    def _dedupe(items: list[Listing]) -> list[Listing]:
        result: dict[str, Listing] = {}
        for item in items:
            result[item.external_id] = item
        return list(result.values())
