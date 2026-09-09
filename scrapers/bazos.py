from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag

from models import Listing
from utils import absolute, clean_text, parse_int, stable_id
from .base import Scraper


class BazosAutoScraper(Scraper):
    source = "bazos_auto"

    # Obvious non-car classifieds that often share a model name.
    EXCLUDE = ("pneu", "pneumatik", "disk", "kolo", "kola", "alu", "díly", "dily", "náhradní", "nahradni", "motor na", "převodovk")

    def parse(self, html: str, search_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[Listing] = []
        seen: set[str] = set()

        for link in soup.select('a[href*="/inzerat/"]'):
            href = link.get("href")
            if not href:
                continue
            url = absolute(search_url, href)
            if "bazos.cz" not in url:
                continue

            card = self._card(link)
            text = clean_text(card.get_text(" ", strip=True))
            title = clean_text(link.get_text(" ", strip=True))
            if len(title) < 4:
                heading = card.find(["h2", "h3"])
                title = clean_text(heading.get_text(" ", strip=True)) if isinstance(heading, Tag) else ""
            if not title:
                continue

            low = f"{title} {text}".lower()
            if any(x in low for x in self.EXCLUDE):
                continue

            price_match = re.search(r"([\d\s\u00a0\u202f]+)\s*Kč", text, re.I)
            # Bazos descriptions use many forms: rok 2020, r.v. 2020, 5/2020.
            year_match = re.search(r"(?:rok|r\.?\s*v\.?|reg\.?|registrace)?\s*[:.]?\s*(?:\d{1,2}/)?\s*\b(19\d{2}|20\d{2})\b", text, re.I)
            km_match = re.search(r"(?:najeto|nájezd|naj\.?|km)\s*[:.]?\s*([\d\s\u00a0\u202f]{3,})\s*(?:km)?", text, re.I)
            if not km_match:
                km_match = re.search(r"([\d\s\u00a0\u202f]{4,})\s*km\b", text, re.I)

            # Deliberately strict: Bazos mixes cars, parts and accessories. We only
            # accept results where the ad itself exposes price + year + mileage.
            if not (price_match and year_match and km_match):
                continue

            ext_id = stable_id(self.source, url)
            if ext_id in seen:
                continue

            fuel = next((x for x in ["hybrid", "benzín", "benzin", "nafta", "diesel", "elektro", "LPG", "CNG"] if x.lower() in low), None)
            transmission = next((x for x in ["automat", "manuál", "manual"] if x in low), None)

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
                year=int(year_match.group(1)),
                mileage_km=parse_int(km_match.group(1)),
                fuel=fuel,
                transmission=transmission,
                image_url=image_url,
            ))
            seen.add(ext_id)

        return out

    @staticmethod
    def _card(link: Tag) -> Tag:
        node = link
        best = link
        for _ in range(8):
            parent = node.parent
            if not isinstance(parent, Tag):
                break
            node = parent
            txt = clean_text(node.get_text(" ", strip=True))
            if "Kč" in txt:
                best = node
                # Bazoš result cards are commonly div.inzeraty; otherwise the
                # nearest priced ancestor is safer than climbing into all results.
                classes = node.get("class", [])
                if "inzeraty" in classes or node.name in ("article", "li"):
                    return node
                return node
        return best
