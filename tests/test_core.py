import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from db import Database
from models import Listing
from scrapers.sauto import SautoScraper
from scrapers.mobile_de import MobileDeScraper
from scrapers.tipcars import TipCarsScraper
from scrapers.bazos import BazosAutoScraper
from scrapers.carvago import CarvagoScraper
from dedupe import is_probable_duplicate
from matcher import evaluate
from main import _send_and_log, _telegram, run_once, should_notify
from scrapers import SCRAPERS
from db import Change
from utils import detect_fuel, parse_mileage_km
from validation import apply_review_state


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

    def test_bazos_recognizes_dci_as_diesel(self):
        html = """<div class="inzeraty"><h2><a href="/inzerat/223786072/renault-koleos-20-dci.php">Renault Koleos 2,0 DCI Initiale Paris</a></h2><div>rok 2019, najeto 105 000 km, automat</div><div>459 000 Kč</div></div>"""
        items = BazosAutoScraper(None).parse(html, "https://auto.bazos.cz/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].fuel, "Diesel")

    def test_bazos_keeps_full_mileage_with_common_separators(self):
        for position, written in enumerate(("219 000", "219.000", "219000", "219 tis. km", "219 tkm"), 1):
            with self.subTest(written=written):
                html = f"""<div class="inzeraty"><h2><a href="/inzerat/223786{position:03d}/toyota-rav4.php">Toyota RAV4 2.5 Hybrid automat</a></h2><div>rok 2020, najeto {written}</div><div>459 000 Kč</div></div>"""
                items = BazosAutoScraper(None).parse(html, "https://auto.bazos.cz/")
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].mileage_km, 219000)

    def test_mileage_parser_rejects_bare_three_digit_fragment(self):
        self.assertIsNone(parse_mileage_km("najeto 219, servis proveden"))

    def test_tipcars_separates_price_year_and_mileage(self):
        html = '''<div class="advertisement">
        <section class="advertisement-name__title"><a href="/santa-fe-123.html"><h3>Hyundai Santa Fe</h3></a><span>Luxury 4x4</span></section>
        <div class="advertisement-name__price">548 000 Kč <span>452 893 Kč bez DPH</span></div>
        <div title="V provozu od/Rok výroby">2019</div><div title="Tachometr">116 800 km</div>
        <a href="/santa-fe-123.html">Foto</a></div>
        <div class="advertisement"><a href="/santa-fe-456.html">Hyundai Santa Fe</a>
        <div class="advertisement-name__price">699 900 Kč</div><div>2023</div><div>5 km</div></div>'''
        items = TipCarsScraper(None).parse(html, "https://www.tipcars.com/hyundai-santa-fe")
        self.assertEqual(len(items), 2)
        self.assertEqual([(x.price, x.year, x.mileage_km) for x in items],
                         [(548000, 2019, 116800), (699900, 2023, 5)])
        self.assertIn('Luxury 4x4', items[0].title)

    def test_tipcars_does_not_treat_phev_range_as_mileage(self):
        html = '''<div class="advertisement advertisement--small-img"
        data-measure-data-value='["advertise", {"data": {"odometer": 212000}}, "signature"]'>
        <section class="advertisement-name__title">
          <a href="/ford-kuga/suv/hybridni-benzin/ford-kuga-2-5-phev-st-line-50km-dojezd-16007496.html"><h3>Ford Kuga</h3></a>
          <p>2.5 phev, ST-LINE,50KM DOJEZD</p>
        </section>
        <div class="advertisement-name__price">429 000 Kč</div>
        <div>2024, 212 000 km, 165 kW, hybridní - benzin</div>
        </div>'''
        items = TipCarsScraper(None).parse(html, "https://www.tipcars.com/ford-kuga")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].mileage_km, 212000)
        self.assertNotEqual(items[0].mileage_km, 50)

    def test_tipcars_text_fallback_ignores_range_line(self):
        html = '''<div class="advertisement">
        <section class="advertisement-name__title"><a href="/ford-kuga-123.html"><h3>Ford Kuga</h3></a>
        <p>PHEV 50 km dojezd</p></section>
        <div class="advertisement-name__price">429 000 Kč</div>
        <div>2024, 98 500 km, hybridní benzin</div></div>'''
        items = TipCarsScraper(None).parse(html, "https://www.tipcars.com/ford-kuga")
        self.assertEqual(items[0].mileage_km, 98500)

    def test_tipcars_does_not_borrow_neighbour_price(self):
        html = '''<main><article><a href="/first.html">Hyundai Santa Fe</a>
        <div>2019</div><div>90 000 km</div></article>
        <article><a href="/second.html">Kia Sorento</a><div>2020</div>
        <div>99 000 km</div><strong>499 000 Kč</strong></article></main>'''
        items = TipCarsScraper(None).parse(html, "https://www.tipcars.com/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, 'Kia Sorento')
        self.assertEqual(items[0].price, 499000)

    def test_mobile_jsonld(self):
        html = '''<script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"item":{"name":"BMW 330i Touring","url":"https://suchen.mobile.de/fahrzeuge/details.html?id=987654321","offers":{"price":"28990","priceCurrency":"EUR"}}}]}</script>'''
        items = MobileDeScraper(None).parse(html, "https://suchen.mobile.de/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].price, 28990)

    def test_carvago_next_data_and_original_identity(self):
        html = '''<script id="__NEXT_DATA__" type="application/json">{
          "props":{"pageProps":{"searchResults":{"cars":[{
            "id":"87033559","slug":"toyota-rav-4-2-5-team-145-kw",
            "title":"Toyota RAV 4 2.5 Team 145 kW","price":472990,
            "price_currency":{"name":"CZK"},"registration_date":"2019-01-01",
            "mileage":128237,"source_name":"mobile_de",
            "external_id":"mobile_de-123456789","vin":"JT123",
            "location_country":{"name":"Německo"},"main_image":"https://img/1.jpg",
            "catalog_features":[
              {"const_key":"FUELTYPE_HYBRID","label":"Hybrid"},
              {"const_key":"TRANSMISSION_AUTOMATIC","label":"Automat"},
              {"const_key":"FEATURE_CRUISECONTROL_ADAPTIVE","label":"Adaptivní tempomat"}
            ]
          }]}}}}</script>'''
        items = CarvagoScraper(None).parse(html, "https://carvago.com/cs/auta/toyota/rav4")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.external_id, "carvago:87033559")
        self.assertEqual(item.canonical_id, "mobile_de:123456789")
        self.assertEqual((item.price, item.year, item.mileage_km), (472990, 2019, 128237))
        self.assertEqual((item.fuel, item.transmission), ("Hybridní", "Automat"))
        self.assertIn("Adaptivní tempomat", item.equipment_text)

    def test_cross_source_dedupe_exact_and_conservative_fallback(self):
        direct = Listing(
            "mobile_de", "mobile_de:123", "https://mobile/123", "Toyota RAV4 2.5 Hybrid 160 kW",
            price=500000, currency="CZK", year=2020, mileage_km=80000,
            fuel="Hybrid", transmission="Automat", model="Toyota RAV4",
        )
        aggregator = Listing(
            "carvago", "carvago:999", "https://carvago/999", "Toyota RAV 4 2.5 Hybrid 160 kW",
            price=530000, currency="CZK", year=2020, mileage_km=80000,
            fuel="Hybrid", transmission="Automat", model="Toyota RAV4",
            canonical_id="mobile_de:123",
        )
        self.assertTrue(is_probable_duplicate(direct, aggregator))

        fallback = Listing(
            "carvago", "carvago:997", "https://carvago/997", "Toyota RAV 4 2.5 Hybrid 160 kW",
            price=530000, currency="CZK", year=2020, mileage_km=80005,
            fuel="Hybridní", transmission="Automatická", model="Toyota RAV4",
        )
        self.assertTrue(is_probable_duplicate(direct, fallback))

        different_car = Listing(
            "carvago", "carvago:998", "https://carvago/998", "Toyota RAV 4 2.5 Hybrid 160 kW",
            price=530000, currency="CZK", year=2020, mileage_km=86000,
            fuel="Hybrid", transmission="Automat", model="Toyota RAV4",
        )
        self.assertFalse(is_probable_duplicate(direct, different_car))

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
            db.conn.close()

    def test_database_suppresses_cross_source_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(str(Path(d) / "test.sqlite3"))
            direct = Listing(
                "mobile_de", "mobile_de:123", "https://mobile/123", "Toyota RAV4 Hybrid",
                price=500000, currency="CZK", year=2020, mileage_km=80000,
                model="Toyota RAV4",
            )
            duplicate = Listing(
                "carvago", "carvago:999", "https://carvago/999", "Toyota RAV4 Hybrid",
                price=525000, currency="CZK", year=2020, mileage_km=80000,
                model="Toyota RAV4", canonical_id="mobile_de:123",
            )
            self.assertEqual(db.upsert(direct).kind, "new")
            self.assertIsNone(db.upsert(duplicate))
            row = db.conn.execute(
                "SELECT alternative_urls FROM listings WHERE external_id='mobile_de:123'"
            ).fetchone()
            self.assertIn("https://carvago/999", row["alternative_urls"])
            db.conn.close()

    def test_partial_new_search_stays_uninitialized_after_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "test.sqlite3")
            db = Database(path)
            search = {"source": "partial", "name": "Toyota RAV4"}
            db.upsert(Listing("partial", "partial:1", "https://x/1", "Car"))
            self.assertFalse(db.search_is_initialized(search))
            db.conn.close()

            reopened = Database(path)
            self.assertFalse(reopened.search_is_initialized(search))
            reopened.conn.close()

    def test_new_source_is_seeded_silently_then_notifies(self):
        class FakeScraper:
            items = [Listing(
                "new_source", "new_source:1", "https://x/1", "Toyota RAV4 Hybrid",
                price=500000, currency="CZK", year=2020, mileage_km=80000,
                fuel="Hybrid", transmission="Automat",
            )]

            def __init__(self, client):
                pass

            def scrape(self, url):
                return list(self.items)

            def enrich(self, item, max_chars=14000):
                return item

        cfg = {
            "telegram": {"enabled": False},
            "ai": {"enabled": False},
            "filters": {"max_price": 550000, "min_year": 2019, "max_mileage_km": 130000},
            "vehicle_rules": {"Toyota RAV4": {"aliases": ["Toyota RAV4"]}},
            "searches": [{"name": "test", "source": "new_source", "url": "https://x"}],
        }
        with tempfile.TemporaryDirectory() as d, patch.dict(SCRAPERS, {"new_source": FakeScraper}):
            db = Database(str(Path(d) / "test.sqlite3"))
            self.assertEqual(run_once(cfg, db), 0)
            self.assertTrue(db.search_is_initialized(cfg["searches"][0]))

            FakeScraper.items.append(Listing(
                "new_source", "new_source:2", "https://x/2", "Toyota RAV4 Hybrid",
                price=490000, currency="CZK", year=2021, mileage_km=70000,
                fuel="Hybrid", transmission="Automat",
            ))
            self.assertEqual(run_once(cfg, db), 1)
            db.conn.close()

    def test_dry_run_does_not_modify_database_or_search_state(self):
        class FakeScraper:
            def __init__(self, client):
                pass

            def scrape(self, url):
                return [Listing(
                    "preview", "preview:1", "https://x/1", "Toyota RAV4 Hybrid",
                    price=500000, currency="CZK", year=2021, mileage_km=80000,
                    fuel="Hybridní", transmission="Automat",
                )]

            def enrich(self, item, max_chars=14000):
                return item

        cfg = {
            "telegram": {"enabled": False}, "ai": {"enabled": False},
            "filters": {"max_price": 550000, "min_year": 2019, "max_mileage_km": 130000},
            "vehicle_rules": {"Toyota RAV4": {"aliases": ["Toyota RAV4"]}},
            "searches": [{"name": "Toyota RAV4", "source": "preview", "url": "https://x"}],
        }
        with tempfile.TemporaryDirectory() as d, patch.dict(SCRAPERS, {"preview": FakeScraper}):
            db = Database(str(Path(d) / "test.sqlite3"))
            self.assertEqual(run_once(cfg, db, dry_run=True), 1)
            self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 0)
            self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM search_state").fetchone()[0], 0)
            self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0], 0)
            db.conn.close()

    def test_each_search_is_initialized_independently(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(str(Path(d) / "test.sqlite3"))
            rav4 = {"source": "same", "name": "Toyota RAV4"}
            xc60 = {"source": "same", "name": "Volvo XC60"}
            db.mark_search_success(rav4)
            self.assertTrue(db.search_is_initialized(rav4))
            self.assertFalse(db.search_is_initialized(xc60))
            db.conn.close()

    @patch("notifier.httpx.post")
    def test_successful_notifications_are_logged_per_recipient(self, post):
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {"ok": True}
        cfg = {"telegram": {"enabled": True, "recipients": [
            {"name": "first", "bot_token_env": "BOT_1", "chat_id_env": "CHAT_1"},
            {"name": "second", "bot_token_env": "BOT_2", "chat_id_env": "CHAT_2"},
        ]}}
        item = Listing("sauto", "sauto:log", "https://x/log", "Toyota RAV4")
        with tempfile.TemporaryDirectory() as d, patch.dict(
            "os.environ", {"BOT_1": "token-1", "CHAT_1": "chat-1", "BOT_2": "token-2", "CHAT_2": "chat-2"}
        ):
            db = Database(str(Path(d) / "test.sqlite3"))
            change = Change("new", item)
            _send_and_log(_telegram(cfg), db, change)
            rows = db.conn.execute(
                "SELECT recipient, status FROM notification_log ORDER BY id"
            ).fetchall()
            self.assertEqual([(row["recipient"], row["status"]) for row in rows],
                             [("first", "sent"), ("second", "sent")])
            db.conn.close()

    def test_suspicious_listing_is_marked_for_review(self):
        item = Listing(
            "bazos_auto", "bazos:review", "https://x/review", "Toyota RAV4 Hybrid",
            year=2028, mileage_km=219, fuel="Hybridní",
            detail_text="Rok výroby: 2019, najeto: 219 000 km",
        )
        reasons = apply_review_state(item, current_year=2026)
        self.assertTrue(item.review_required)
        self.assertTrue(any("rok" in reason for reason in reasons))
        self.assertTrue(any("nájezdu" in reason for reason in reasons))

        transmission_conflict = Listing(
            "sauto", "sauto:review", "https://x/review-2",
            "Toyota RAV4 2.5 Hybrid manuál", year=2022, mileage_km=80000,
            fuel="Hybridní", transmission="Automatická",
        )
        reasons = apply_review_state(transmission_conflict, current_year=2026)
        self.assertTrue(any("převodovky" in reason for reason in reasons))

    def test_database_returns_only_latest_new_batch(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(str(Path(d) / "test.sqlite3"))
            rows = [
                ("old", "2026-09-10T08:00:00+00:00", 50),
                ("new-1", "2026-09-10T10:00:00+00:00", 80),
                ("new-2", "2026-09-10T10:00:20+00:00", 90),
            ]
            for external_id, first_seen, score in rows:
                db.conn.execute(
                    """INSERT INTO listings (
                        external_id, source, url, title, score, first_seen, last_seen
                    ) VALUES (?, 'sauto', ?, ?, ?, ?, ?)""",
                    (external_id, f"https://x/{external_id}", external_id, score, first_seen, first_seen),
                )
            db.conn.commit()

            latest = db.latest_new_listings()

            self.assertEqual([item.external_id for item in latest], ["new-2", "new-1"])
            db.conn.close()

    def test_vehicle_filter_and_scoring(self):
        cfg = {
            "filters": {
                "max_price": 550000, "min_year": 2019, "max_mileage_km": 130000,
                "ideal_year": 2020, "ideal_mileage_km": 100000, "ideal_price": 500000,
                "excluded_fuels": ["nafta", "diesel"],
                "excluded_transmissions": ["manuální", "manuál", "manual"],
            },
            "vehicle_rules": {
                "Toyota RAV4": {"aliases": ["Toyota RAV4"]},
                "Volvo XC60": {"aliases": ["Volvo XC60"], "max_price": 650000, "min_year": 2018, "max_mileage_km": 140000},
            },
        }
        good = Listing("sauto", "1", "https://x/1", "Toyota RAV4 2.5 Hybrid", price=520000, currency="CZK", year=2020, mileage_km=108000)
        self.assertTrue(evaluate(good, cfg).accepted)

        too_expensive = Listing("sauto", "2", "https://x/2", "Toyota RAV4", price=560000, currency="CZK", year=2021, mileage_km=70000)
        self.assertFalse(evaluate(too_expensive, cfg).accepted)

        premium = Listing("sauto", "3", "https://x/3", "Volvo XC60 D4", price=525000, currency="CZK", year=2018, mileage_km=125000)
        self.assertFalse(evaluate(premium, cfg).accepted)

        premium_hybrid = Listing("sauto", "7", "https://x/7", "Volvo XC60 T8 PHEV", price=625000, currency="CZK", year=2020, mileage_km=110000)
        self.assertTrue(evaluate(premium_hybrid, cfg).accepted)

        premium_over_limit = Listing("sauto", "8", "https://x/8", "Volvo XC60 T8 PHEV", price=660000, currency="CZK", year=2020, mileage_km=110000)
        self.assertFalse(evaluate(premium_over_limit, cfg).accepted)

        structured_hybrid = Listing("carvago", "9", "https://x/9", "Volvo XC60 T6 AWD", price=640000, currency="CZK", year=2022, mileage_km=120000, fuel="Hybridní", transmission="Automat", detail_text="Další nabídky: diesel TDI")
        self.assertTrue(evaluate(structured_hybrid, cfg).accepted)

        old_mainstream = Listing("sauto", "4", "https://x/4", "Toyota RAV4", price=450000, currency="CZK", year=2018, mileage_km=90000)
        self.assertFalse(evaluate(old_mainstream, cfg).accepted)

        diesel = Listing("sauto", "5", "https://x/5", "Toyota RAV4 2.0 D-4D", price=450000, currency="CZK", year=2020, mileage_km=90000, fuel="Nafta")
        result = evaluate(diesel, cfg)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "excluded fuel Nafta")

        manual = Listing("sauto", "6", "https://x/6", "Toyota RAV4 2.5 Hybrid", price=450000, currency="CZK", year=2020, mileage_km=90000, fuel="Hybridní", transmission="Manuální")
        result = evaluate(manual, cfg)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "excluded transmission Manuální")

    def test_engine_badges_detect_fuel(self):
        diesel_titles = [
            "Renault Koleos 2.0 dCi", "Škoda Kodiaq 2.0 TDI",
            "Peugeot 5008 BlueHDi", "Hyundai Tucson 1.6 CRDi",
            "Ford Kuga 2.0 TDCi", "Mazda CX-5 SKYACTIV-D",
            "Toyota RAV4 D-4D", "Honda CR-V i-DTEC",
            "Mitsubishi Outlander 2.2 DI-D", "Volvo XC60 D4",
            "Fiat 500X MultiJet", "Opel Insignia CDTI",
            "Mercedes GLC 220d", "BMW X3 xDrive20d",
        ]
        for title in diesel_titles:
            with self.subTest(title=title):
                self.assertEqual(detect_fuel(title), "Diesel")

        self.assertEqual(detect_fuel("Toyota RAV4 2.5 PHEV"), "Plug-in hybrid")
        self.assertEqual(detect_fuel("Honda CR-V e:HEV"), "Hybridní")
        self.assertEqual(detect_fuel("Ford Kuga 1.5 EcoBoost"), "Benzín")

    def test_only_new_listings_are_notified_by_default(self):
        item = Listing("sauto", "sauto:1", "https://x/1", "Toyota RAV4", price=500000, currency="CZK")
        self.assertTrue(should_notify(Change("new", item), {}))
        self.assertFalse(should_notify(Change("price_change", item, 520000), {}))
        cfg = {"notifications": {"new_listings": True, "price_changes": False}}
        self.assertTrue(should_notify(Change("new", item), cfg))
        self.assertFalse(should_notify(Change("price_change", item, 520000), cfg))

    @patch("notifier.httpx.post")
    def test_telegram_sends_to_both_configured_chats(self, post):
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {"ok": True}
        cfg = {
            "telegram": {
                "enabled": True,
                "recipients": [
                    {"name": "first", "bot_token_env": "BOT_1", "chat_id_env": "CHAT_1"},
                    {"name": "second", "bot_token_env": "BOT_2", "chat_id_env": "CHAT_2", "optional": True},
                ],
            }
        }
        item = Listing("sauto", "1", "https://x/1", "Toyota RAV4")

        with patch.dict("os.environ", {"BOT_1": "token-1", "CHAT_1": "chat-1", "BOT_2": "token-2", "CHAT_2": "chat-2"}):
            _telegram(cfg).send(Change("new", item))

        self.assertEqual(post.call_count, 2)
        self.assertIn("bottoken-1/sendMessage", post.call_args_list[0].args[0])
        self.assertEqual(post.call_args_list[0].kwargs["json"]["chat_id"], "chat-1")
        self.assertIn("bottoken-2/sendMessage", post.call_args_list[1].args[0])
        self.assertEqual(post.call_args_list[1].kwargs["json"]["chat_id"], "chat-2")

        post.reset_mock()
        with patch.dict("os.environ", {"BOT_1": "token-1", "CHAT_1": "chat-1", "BOT_2": "token-2", "CHAT_2": "chat-2"}):
            _telegram(cfg).send(Change("new", item), recipient_name="second")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], "chat-2")

    @patch("notifier.httpx.post")
    def test_optional_second_telegram_chat_can_be_empty(self, post):
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {"ok": True}
        cfg = {
            "telegram": {
                "enabled": True,
                "recipients": [
                    {"name": "first", "bot_token_env": "BOT_1", "chat_id_env": "CHAT_1"},
                    {"name": "second", "bot_token_env": "BOT_2", "chat_id_env": "CHAT_2", "optional": True},
                ],
            }
        }

        with patch.dict("os.environ", {"BOT_1": "token-1", "CHAT_1": "chat-1"}, clear=True):
            _telegram(cfg).send_test()

        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
