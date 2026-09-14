from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup, Tag

from models import Listing
from utils import absolute, clean_text, detect_fuel, parse_int
from .base import Scraper


class CarvagoScraper(Scraper):
    source = "carvago"

    def parse(self, html: str, search_url: str) -> list[Listing]:
        soup = BeautifulSoup(html, "html.parser")
        items = self._next_data(soup, search_url)
        return items if items else self._html_fallback(soup, search_url)

    def _next_data(self, soup: BeautifulSoup, search_url: str) -> list[Listing]:
        script = soup.find("script", id="__NEXT_DATA__")
        if not isinstance(script, Tag):
            return []
        try:
            data = json.loads(script.string or "")
            cars = data["props"]["pageProps"]["searchResults"]["cars"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return []

        out: list[Listing] = []
        for car in cars if isinstance(cars, list) else []:
            if not isinstance(car, dict) or not car.get("id") or not car.get("title"):
                continue
            car_id = str(car["id"])
            slug = str(car.get("slug") or "auto")
            features = car.get("catalog_features") or []
            feature_map = {
                str(x.get("const_key")): clean_text(x.get("label"))
                for x in features if isinstance(x, dict)
            }
            fuel_label = next((v for k, v in feature_map.items() if k.startswith("FUELTYPE_")), None)
            fuel = detect_fuel(f"{car['title']} {fuel_label or ''}") or fuel_label
            transmission = next((v for k, v in feature_map.items() if k.startswith("TRANSMISSION_")), None)
            equipment = [v for k, v in feature_map.items() if k.startswith("FEATURE_") and v]
            registration = str(car.get("registration_date") or "")
            currency = car.get("price_currency") or {}
            country = car.get("location_country") or {}

            out.append(Listing(
                source=self.source,
                external_id=f"{self.source}:{car_id}",
                url=absolute(search_url, f"/cs/auto/{car_id}/{slug}"),
                title=clean_text(str(car["title"]))[:220],
                price=car.get("price") if isinstance(car.get("price"), int) else parse_int(str(car.get("price") or "")),
                currency=currency.get("name") if isinstance(currency, dict) else None,
                year=int(registration[:4]) if re.match(r"^(?:19|20)\d{2}", registration) else None,
                mileage_km=car.get("mileage") if isinstance(car.get("mileage"), int) else parse_int(str(car.get("mileage") or "")),
                fuel=fuel,
                transmission=transmission,
                location=country.get("name") if isinstance(country, dict) else None,
                image_url=car.get("main_image"),
                equipment_text=", ".join(equipment[:60]) or None,
                canonical_id=self._canonical_id(car),
                vin=clean_text(str(car.get("vin") or "")) or None,
            ))
        return out

    @staticmethod
    def _canonical_id(car: dict) -> str | None:
        source = clean_text(str(car.get("source_name") or "")).lower()
        external = clean_text(str(car.get("external_id") or ""))
        if not source or not external or source == "carvago":
            return None
        prefix = f"{source}-"
        if external.lower().startswith(prefix):
            external = external[len(prefix):]
        return f"{source}:{external}"

    def _html_fallback(self, soup: BeautifulSoup, search_url: str) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        for link in soup.select('a[href*="/cs/auto/"]'):
            href = link.get("href")
            if not href:
                continue
            match = re.search(r"/cs/auto/(\d+)", str(href))
            if not match:
                continue
            external_id = f"{self.source}:{match.group(1)}"
            if external_id in seen:
                continue
            text = clean_text(link.get_text(" ", strip=True))
            heading = link.find(["h2", "h3", "h4"])
            title = clean_text(heading.get_text(" ", strip=True)) if isinstance(heading, Tag) else ""
            price_match = re.search(r"([\d\s\u00a0\u202f]+)\s*Kč", text, re.I)
            date_match = re.search(r"\b\d{1,2}/(19\d{2}|20\d{2})\b", text)
            km_match = re.search(r"([\d\s\u00a0\u202f]+)\s*km\b", text, re.I)
            if not title or not (price_match and date_match and km_match):
                continue
            low = text.lower()
            image = link.find("img")
            out.append(Listing(
                source=self.source,
                external_id=external_id,
                url=absolute(search_url, str(href)),
                title=title[:220],
                price=parse_int(price_match.group(1)),
                currency="CZK",
                year=int(date_match.group(1)),
                mileage_km=parse_int(km_match.group(1)),
                fuel=detect_fuel(f"{title} {text}"),
                transmission=next((x for x in ("Automat", "Manuál") if x.lower() in low), None),
                image_url=(image.get("src") or image.get("data-src")) if isinstance(image, Tag) else None,
            ))
            seen.add(external_id)
        return out
