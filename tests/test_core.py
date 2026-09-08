import tempfile
import unittest
from pathlib import Path

from db import Database
from models import Listing
from scrapers.sauto import SautoScraper
from scrapers.mobile_de import MobileDeScraper
from scrapers.tipcars import TipCarsScraper
from scrapers.bazos import BazosAutoScraper
from matcher import evaluate
from main import should_notify
from db import Change


class ParserTests(unittest.TestCase):
    def test_sauto(self):
        html = '''<article><a href="/detail/osobni/skoda/octavia/12345678"><h2>Škoda Octavia 2.0 TDI</h2></a><p>2021, 84 200 km, Nafta, Automatická</p><strong>429 900 Kč</strong></article>'''
        items = SautoScraper(None).parse(html, "https://www.sauto.cz/inzerce/osobni")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 429900)
        self.assertEqual(items[0].mileage_km, 84200)
        self.assertEqual(items[0].year, 2021)

    def test_tipcars(self):
        html = """<article><a href="/toyota-rav4/suv/benzin/toyota-rav4-test-123.html"><h2>Toyota RAV4 2.5 Hybrid</h2></a><div>12/2020</div><div>98 500 km</div><strong>529 900 Kč</strong></article>"""
        items = TipCarsScraper(None).parse(html, "https://www.tipcars.com/toyota-rav4")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 529900)
        self.assertEqual(items[0].year, 2020)
        self.assertEqual(items[0].mileage_km, 98500)

    def test_bazos_car_and_parts_filter(self):
        html = """
        <div class="inzeraty"><h2><a href="/inzerat/123456789/toyota-rav4-hybrid.php">Toyota Rav4 Hybrid</a></h2><div>rok 2020, najeto 99 000 km, automat</div><div>529 000 Kč</div></div>
        <div class="inzeraty"><h2><a href="/inzerat/987654321/kola-toyota-rav4.php">ALU kola Toyota RAV4</a></h2><div>rok 2020, 99 000 km</div><div>15 000 Kč</div></div>
        """
        items = BazosAutoScraper(None).parse(html, "https://auto.bazos.cz/inzeraty/toyota-rav4/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 529000)
        self.assertEqual(items[0].year, 2020)
        self.assertEqual(items[0].mileage_km, 99000)

    def test_mobile_jsonld(self):
        html = '''<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"item":{"name":"BMW 330i Touring","url":"https://suchen.mobile.de/fahrzeuge/details.html?id=987654321","offers":{"price":"28990","priceCurrency":"EUR"}}}]}</script>'''
        items = MobileDeScraper(None).parse(html, "https://suchen.mobile.de/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 28990)

    def test_database_changes(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(str(Path(d) / "test.sqlite3"))
            a = Listing("sauto", "sauto:1", "https://x/1", "Car", price=100, currency="CZK")
            self.assertEqual(db.upsert(a).kind, "new")
            self.assertIsNone(db.upsert(a))
            b = Listing("sauto", "sauto:1", "https://x/1", "Car", price=90, currency="CZK")
            ch = db.upsert(b)
            self.assertEqual(ch.kind, "price_change")
            self.assertEqual(ch.old_price, 100)

    def test_vehicle_filter_and_scoring(self):
        cfg = {
            "filters": {
                "max_price": 550000, "min_year": 2019, "max_mileage_km": 130000,
                "ideal_year": 2020, "ideal_mileage_km": 100000, "ideal_price": 500000,
            },
            "vehicle_rules": {
                "Toyota RAV4": {"aliases": ["Toyota RAV4"]},
                "Volvo XC60": {"aliases": ["Volvo XC60"], "min_year": 2018, "max_mileage_km": 140000},
            },
        }
        good = Listing("sauto", "1", "https://x/1", "Toyota RAV4 2.5 Hybrid", price=520000, currency="CZK", year=2020, mileage_km=108000)
        self.assertTrue(evaluate(good, cfg).accepted)

        too_expensive = Listing("sauto", "2", "https://x/2", "Toyota RAV4", price=560000, currency="CZK", year=2021, mileage_km=70000)
        self.assertFalse(evaluate(too_expensive, cfg).accepted)

        premium = Listing("sauto", "3", "https://x/3", "Volvo XC60 D4", price=525000, currency="CZK", year=2018, mileage_km=125000)
        self.assertTrue(evaluate(premium, cfg).accepted)

        old_mainstream = Listing("sauto", "4", "https://x/4", "Toyota RAV4", price=450000, currency="CZK", year=2018, mileage_km=90000)
        self.assertFalse(evaluate(old_mainstream, cfg).accepted)

    def test_only_new_listings_are_notified_by_default(self):
        item = Listing("sauto", "sauto:1", "https://x/1", "Toyota RAV4", price=500000, currency="CZK")
        self.assertTrue(should_notify(Change("new", item), {}))
        self.assertFalse(should_notify(Change("price_change", item, 520000), {}))
        cfg = {"notifications": {"new_listings": True, "price_changes": False}}
        self.assertTrue(should_notify(Change("new", item), cfg))
        self.assertFalse(should_notify(Change("price_change", item, 520000), cfg))


if __name__ == "__main__":
    unittest.main()
