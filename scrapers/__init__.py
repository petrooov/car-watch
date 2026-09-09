from .sauto import SautoScraper
from .mobile_de import MobileDeScraper
from .tipcars import TipCarsScraper
from .bazos import BazosAutoScraper

SCRAPERS = {
    "sauto": SautoScraper,
    "mobile_de": MobileDeScraper,
    "tipcars": TipCarsScraper,
    "bazos_auto": BazosAutoScraper,
}
