from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag

from models import Listing
from utils import absolute, clean_text, detect_fuel, parse_int, stable_id
from .base import Scraper


class TipCarsScraper(Scraper):
    source = "tipcars"

    def parse(self, html: str, search_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[Listing] = []
        seen: set[str] = set()

        # Detail URLs on TipCars end in .html. Avoid navigation/static links by
        # requiring the surrounding block to contain a CZK price and vehicle data.
        for link in soup.select('a[href$=".html"], a[href*=".html?"]'):
            href = link.get("href")
            if not href:
                continue
            url = absolute(search_url, href)
            if "tipcars." not in url:
                continue

            card = self._card(link)
            if card is None:
                continue
            # Preserve field boundaries: otherwise '4x4' + '548 000 Kč'
            # becomes a price of 4 548 000, and year joins the odometer.
            text = card.get_text("\n", strip=True)
            if "Kč" not in text:
                continue

            title = self._title(link, card)
            if not title or len(title) < 4:
                continue

            price_node = card.select_one('.advertisement-name__price')
            price_text = price_node.get_text("\n", strip=True) if price_node else text
            price_match = re.search(r"(\d[\d \u00a0\u202f]*)\s*Kč", price_text, re.I)
            # Prefer a 4-digit production year, including formats such as 2/2020.
            year_node = card.select_one('[title="V provozu od/Rok výroby"]')
            km_node = card.select_one('[title="Tachometr"]')
            year_text = year_node.get_text(" ", strip=True) if year_node else text
            km_text = km_node.get_text(" ", strip=True) if km_node else text
            year_match = re.search(r"(?:\b\d{1,2}/)?\b(19\d{2}|20\d{2})\b", year_text)
            km_match = re.search(r"\b(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d{1,6})\s*km\b", km_text, re.I)
            if not price_match:
                continue

            ext_id = stable_id(self.source, url)
            if ext_id in seen:
                continue

            low = text.lower()
            fuel = detect_fuel(f"{title} {text}")
            transmission = next((x for x in ["automat", "manuál", "manuální"] if x in low), None)

            image = card.find("img")
            image_url = None
            if isinstance(image, Tag):
                image_url = image.get("src") or image.get("data-src")

            out.append(Listing(
                source=self.source,
                external_id=ext_id,
                url=url,
                title=title[:220],
                price=parse_int(price_match.group(1)),
                currency="CZK",
                year=int(year_match.group(1)) if year_match else None,
                mileage_km=parse_int(km_match.group(1)) if km_match else None,
                fuel=fuel,
                transmission=transmission,
                image_url=image_url,
            ))
            seen.add(ext_id)

        return out

    @staticmethod
    def _card(link: Tag) -> Tag | None:
        card = link.find_parent(class_="advertisement")
        if card is not None:
            return card
        node = link
        best = None
        for _ in range(9):
            parent = node.parent
            if not isinstance(parent, Tag):
                break
            node = parent
            # Never borrow fields from a neighbouring listing.
            urls = {str(a.get("href")).split("?")[0] for a in node.select('a[href*=".html"]')}
            if len(urls) > 1:
                break
            txt = clean_text(node.get_text(" ", strip=True))
            if "Kč" in txt:
                best = node
                if re.search(r"\b\d[\d\s\u00a0\u202f]*\s*km\b", txt, re.I):
                    return node
        return best

    @staticmethod
    def _title(link: Tag, card: Tag) -> str:
        title = card.select_one('.advertisement-name__title')
        if title is not None:
            return clean_text(title.get_text(" ", strip=True))
        candidates = [link]
        candidates.extend(card.find_all(["h2", "h3", "h4"], limit=3))
        for node in candidates:
            if isinstance(node, Tag):
                txt = clean_text(node.get_text(" ", strip=True))
                if len(txt) >= 4 and "Kč" not in txt and not txt.lower().startswith(("zavolat", "kontaktovat")):
                    return txt
        return ""
