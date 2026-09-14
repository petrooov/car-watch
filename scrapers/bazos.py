from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag

from models import Listing
from utils import absolute, clean_text, detect_fuel, parse_int, parse_mileage_km, stable_id
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
            raw_text = card.get_text("\n", strip=True)
            text = clean_text(raw_text)
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
            mileage_km = parse_mileage_km(raw_text)

            # Deliberately strict: Bazos mixes cars, parts and accessories. We only
            # accept results where the ad itself exposes price + year + mileage.
            if not (price_match and year_match and mileage_km is not None):
                continue

            ext_id = stable_id(self.source, url)
            if ext_id in seen:
                continue

            fuel = detect_fuel(low)
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
                mileage_km=mileage_km,
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
