from __future__ import annotations

import json
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
            year_match = re.search(r"(?:\b\d{1,2}/)?\b(19\d{2}|20\d{2})\b", year_text)
            mileage_km = self._mileage(card, km_node, text)
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
                mileage_km=mileage_km,
                fuel=fuel,
                transmission=transmission,
                image_url=image_url,
            ))
            seen.add(ext_id)

        return out

    @staticmethod
    def _mileage(card: Tag, km_node: Tag | None, text: str) -> int | None:
        """Read the odometer without confusing electric range in the title.

        Current TipCars cards expose a structured ``odometer`` value in their
        measurement payload. Older layouts use a dedicated Tachometr element.
        The final text fallback examines individual lines and deliberately skips
        lines containing "dojezd" (for example "PHEV, 50KM DOJEZD").
        """
        raw = card.get("data-measure-data-value")
        if raw:
            try:
                payload = json.loads(str(raw))
                measurement = payload[1] if isinstance(payload, list) and len(payload) > 1 else payload
                data = measurement.get("data", {}) if isinstance(measurement, dict) else {}
                odometer = data.get("odometer")
                if isinstance(odometer, (int, float)) and 0 <= odometer <= 2_000_000:
                    return int(odometer)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if isinstance(km_node, Tag):
            match = re.search(r"\b(\d{1,3}(?:[ .\u00a0\u202f]\d{3})+|\d{1,7})\s*km\b",
                              km_node.get_text(" ", strip=True), re.I)
            if match:
                return parse_int(match.group(1))

        pattern = re.compile(r"\b(\d{1,3}(?:[ .\u00a0\u202f]\d{3})+|\d{1,7})\s*km\b", re.I)
        for line in text.splitlines():
            cleaned = clean_text(line)
            if "dojezd" in cleaned.lower():
                continue
            match = pattern.search(cleaned)
            if match:
                return parse_int(match.group(1))
        return None

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
