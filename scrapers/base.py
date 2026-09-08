from __future__ import annotations

from abc import ABC, abstractmethod
import httpx

from models import Listing


class Scraper(ABC):
    source: str

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
