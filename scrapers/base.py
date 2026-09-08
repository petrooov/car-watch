from __future__ import annotations

from abc import ABC, abstractmethod
import re

from bs4 import BeautifulSoup
import httpx

from models import Listing
from utils import clean_text


class Scraper(ABC):
    source: str

    # Nejde o úplný katalog výbavy; jen rychlý výtah pro prompt a Telegram.
    EQUIPMENT_KEYWORDS = (
        "adaptivní tempomat", "tempomat", "head-up", "head up", "hud",
        "mrtvý úhel", "mrtveho uhlu", "blind spot", "360°", "360 kamera",
        "parkovací kamera", "zadní kamera", "kamera", "parkovací senzory",
        "vyhřívaná sedadla", "vyhrivana sedadla", "ventilovaná sedadla",
        "kožená sedadla", "kůže", "keyless", "bezklíčové", "apple carplay",
        "android auto", "navigace", "led", "matrix", "panoramatická střecha",
        "panorama", "elektrické víko", "elektrické páté dveře", "tažné zařízení",
        "tažné", "4x4", "awd", "automatická klimatizace", "dvouzónová",
        "třízónová", "nezávislé topení", "webasto", "memory", "paměť sedadel",
        "lane assist", "udržování v pruhu", "rozpoznávání značek", "isofix",
    )

    def __init__(self, client: httpx.Client):
        self.client = client

    def fetch(self, url: str) -> str:
        response = self.client.get(url, follow_redirects=True)
        response.raise_for_status()
        return response.text

    @abstractmethod
    def parse(self, html: str, search_url: str) -> list[Listing]: ...

    def scrape(self, url: str) -> list[Listing]:
        return self.parse(self.fetch(url), url)

    def enrich(self, item: Listing, max_chars: int = 14_000) -> Listing:
        """Otevře detail inzerátu a uloží čitelný text + nalezenou výbavu.

        Je to záměrně generické, aby fungovalo pro Sauto, TipCars i Bazoš bez
        závislosti na křehkých CSS třídách. Když web detail blokuje, caller má
        fallback na data z výsledkové stránky.
        """
        html = self.fetch(item.url)
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg", "template"]):
            tag.decompose()

        meta_desc = ""
        meta = soup.find("meta", attrs={"name": re.compile("description", re.I)})
        if meta and meta.get("content"):
            meta_desc = clean_text(str(meta.get("content")))

        root = soup.find("main") or soup.body or soup
        visible = clean_text(root.get_text(" ", strip=True))
        combined = clean_text(f"{meta_desc} {visible}")

        # Omezíme payload do AI a zároveň zachováme začátek stránky, kde bývá
        # technika, výbava a popis prodejce.
        item.detail_text = combined[:max_chars]

        low = combined.lower()
        found: list[str] = []
        for keyword in self.EQUIPMENT_KEYWORDS:
            if keyword.lower() in low and keyword not in found:
                found.append(keyword)
        item.equipment_text = ", ".join(found[:35]) or None
        return item
