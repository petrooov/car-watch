from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag

from models import Listing
from utils import absolute, clean_text, parse_int, stable_id
from .base import Scraper


class SautoScraper(Scraper):
    source = "sauto"

    def parse(self, html: str, search_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()
        out: list[Listing] = []

        # Sauto listing links contain /detail/. We intentionally avoid brittle CSS classes.
        for link in soup.select('a[href*="/detail/"]'):
            href = link.get("href")
            if not href:
                continue
            url = absolute(search_url, href)
            ext_id = stable_id(self.source, url)
            if ext_id in seen:
                continue

            card = self._card(link)
            text = clean_text(card.get_text(" ", strip=True))
            title = self._title(link, card)
            if not title:
                continue

            price_match = re.search(r"([\d\s\u00a0\u202f]+)\s*Kč", text, re.I)
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
            km_match = re.search(r"([\d\s\u00a0\u202f]+)\s*km\b", text, re.I)
            transmission = self._contains(text, ["Automatická", "Manuální"])
            fuel = self._contains(text, ["Benzín", "Nafta", "Elektro", "Hybridní", "LPG", "CNG"])

            image = card.find("img")
            image_url = None
            if isinstance(image, Tag):
                image_url = image.get("src") or image.get("data-src")

            out.append(Listing(
                source=self.source,
                external_id=ext_id,
                url=url,
                title=title,
                price=parse_int(price_match.group(1)) if price_match else None,
                currency="CZK" if price_match else None,
                year=int(year_match.group(1)) if year_match else None,
                mileage_km=parse_int(km_match.group(1)) if km_match else None,
                fuel=fuel,
                transmission=transmission,
                image_url=image_url,
            ))
            seen.add(ext_id)
        return out

    @staticmethod
    def _card(link: Tag) -> Tag:
        node = link
        for _ in range(7):
            parent = node.parent
            if not isinstance(parent, Tag):
                break
            node = parent
            txt = clean_text(node.get_text(" ", strip=True))
            if "Kč" in txt and re.search(r"\b\d[\d\s]*\s*km\b", txt, re.I):
                return node
        return link

    @staticmethod
    def _title(link: Tag, card: Tag) -> str:
        for node in (link, card.find(["h2", "h3", "h4"])):
            if isinstance(node, Tag):
                txt = clean_text(node.get_text(" ", strip=True))
                if len(txt) >= 4 and "Kč" not in txt:
                    return txt[:220]
        return ""

    @staticmethod
    def _contains(text: str, choices: list[str]) -> str | None:
        low = text.lower()
        return next((x for x in choices if x.lower() in low), None)
