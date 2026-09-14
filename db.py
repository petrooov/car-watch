from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models import Listing
from dedupe import add_alternative, is_probable_duplicate


@dataclass(slots=True)
class Change:
    kind: str  # new | price_change
    listing: Listing
    old_price: int | None = None


class Database:
    def __init__(self, path: str = "car_watch.sqlite3"):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        had_source_state = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_state'"
        ).fetchone() is not None
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS listings (
            external_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            price INTEGER,
            currency TEXT,
            year INTEGER,
            mileage_km INTEGER,
            fuel TEXT,
            transmission TEXT,
            location TEXT,
            image_url TEXT,
            model TEXT,
            score INTEGER,
            match_reason TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            price INTEGER,
            seen_at TEXT NOT NULL,
            FOREIGN KEY(external_id) REFERENCES listings(external_id)
        );
        CREATE TABLE IF NOT EXISTS source_state (
            source TEXT PRIMARY KEY,
            initialized_at TEXT NOT NULL
        );
        """)
        # Lightweight migration for databases created by older versions.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(listings)")}
        for name, decl in (
            ("model", "TEXT"), ("score", "INTEGER"), ("match_reason", "TEXT"),
            ("canonical_id", "TEXT"), ("vin", "TEXT"), ("alternative_urls", "TEXT"),
        ):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE listings ADD COLUMN {name} {decl}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_canonical_id ON listings(canonical_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_vin ON listings(vin)")
        if not had_source_state:
            # Jednorázová migrace: staré databáze už mají zavedené zdroje.
            # Při dalších startech se záměrně neodvozuje stav z listings,
            # protože neúplný první scrape mohl uložit jen část zdroje.
            now = datetime.now(timezone.utc).isoformat()
            self.conn.execute(
                """
                INSERT OR IGNORE INTO source_state(source, initialized_at)
                SELECT DISTINCT source, ? FROM listings
                """,
                (now,),
            )
        self.conn.commit()

    def source_is_initialized(self, source: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM source_state WHERE source = ?", (source,)
        ).fetchone() is not None

    def mark_source_initialized(self, source: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR IGNORE INTO source_state(source, initialized_at) VALUES (?, ?)",
            (source, now),
        )
        self.conn.commit()

    def upsert(self, item: Listing) -> Change | None:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT * FROM listings WHERE external_id = ?", (item.external_id,)
        ).fetchone()

        # Exact cross-source identity: original marketplace ID exposed by an
        # aggregator, or VIN when both sources publish it.
        if row is None and item.canonical_id:
            row = self.conn.execute(
                "SELECT * FROM listings WHERE external_id = ? OR canonical_id = ? LIMIT 1",
                (item.canonical_id, item.canonical_id),
            ).fetchone()
        if row is None and item.vin:
            row = self.conn.execute(
                "SELECT * FROM listings WHERE vin = ? LIMIT 1", (item.vin,)
            ).fetchone()

        # Conservative persisted fallback. It only inspects another source with
        # the same model/year and a nearly identical odometer reading.
        if row is None and item.model and item.year and item.mileage_km is not None:
            tolerance = max(10, round(item.mileage_km * 0.001))
            candidates = self.conn.execute(
                """
                SELECT * FROM listings
                WHERE source != ? AND model = ? AND year = ?
                  AND mileage_km BETWEEN ? AND ?
                """,
                (item.source, item.model, item.year,
                 item.mileage_km - tolerance, item.mileage_km + tolerance),
            ).fetchall()
            row = next((candidate for candidate in candidates
                        if is_probable_duplicate(self._listing(candidate), item)), None)

        if row is None:
            self.conn.execute("""
                INSERT INTO listings (
                    external_id, source, url, title, price, currency, year,
                    mileage_km, fuel, transmission, location, image_url, model, score, match_reason,
                    canonical_id, vin, alternative_urls, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.external_id, item.source, item.url, item.title, item.price,
                item.currency, item.year, item.mileage_km, item.fuel,
                item.transmission, item.location, item.image_url, item.model, item.score,
                item.match_reason, item.canonical_id, item.vin,
                json.dumps(item.alternative_urls or [], ensure_ascii=False), now, now
            ))
            self.conn.execute(
                "INSERT INTO price_history(external_id, price, seen_at) VALUES (?, ?, ?)",
                (item.external_id, item.price, now),
            )
            self.conn.commit()
            return Change("new", item)

        # A different source representing the same physical listing must not
        # look like a price change. Preserve the original row and remember the
        # additional link for later resends.
        if row["external_id"] != item.external_id:
            stored = self._listing(row)
            add_alternative(stored, item)
            self.conn.execute(
                """
                UPDATE listings SET canonical_id=COALESCE(canonical_id, ?),
                    vin=COALESCE(vin, ?), alternative_urls=?, last_seen=?
                WHERE external_id=?
                """,
                (item.canonical_id, item.vin,
                 json.dumps(stored.alternative_urls or [], ensure_ascii=False),
                 now, row["external_id"]),
            )
            self.conn.commit()
            return None

        old_price = row["price"]
        self.conn.execute("""
            UPDATE listings SET
                url=?, title=?, price=?, currency=?, year=?, mileage_km=?, fuel=?,
                transmission=?, location=?, image_url=?, model=?, score=?, match_reason=?,
                canonical_id=?, vin=?, alternative_urls=?, last_seen=?
            WHERE external_id=?
        """, (
            item.url, item.title, item.price, item.currency, item.year,
            item.mileage_km, item.fuel, item.transmission, item.location,
            item.image_url, item.model, item.score, item.match_reason,
            item.canonical_id, item.vin,
            json.dumps(item.alternative_urls or [], ensure_ascii=False),
            now, item.external_id
        ))

        if item.price is not None and old_price != item.price:
            self.conn.execute(
                "INSERT INTO price_history(external_id, price, seen_at) VALUES (?, ?, ?)",
                (item.external_id, item.price, now),
            )
            self.conn.commit()
            return Change("price_change", item, old_price)

        self.conn.commit()
        return None

    @staticmethod
    def _listing(row: sqlite3.Row) -> Listing:
        alternatives = []
        try:
            alternatives = [tuple(x) for x in json.loads(row["alternative_urls"] or "[]")]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return Listing(
            source=row["source"], external_id=row["external_id"], url=row["url"],
            title=row["title"], price=row["price"], currency=row["currency"],
            year=row["year"], mileage_km=row["mileage_km"], fuel=row["fuel"],
            transmission=row["transmission"], location=row["location"],
            image_url=row["image_url"], model=row["model"], score=row["score"],
            match_reason=row["match_reason"], canonical_id=row["canonical_id"],
            vin=row["vin"], alternative_urls=alternatives or None,
        )

    def latest_new_listings(self, batch_window_seconds: int = 60) -> list[Listing]:
        """Return the newest burst of first-seen listings from the database."""
        newest = self.conn.execute(
            "SELECT MAX(first_seen) AS newest FROM listings"
        ).fetchone()["newest"]
        if newest is None:
            return []

        rows = self.conn.execute(
            """
            SELECT * FROM listings
            WHERE julianday(first_seen) >= julianday(?) - (? / 86400.0)
            ORDER BY score DESC, first_seen ASC
            """,
            (newest, max(1, int(batch_window_seconds))),
        ).fetchall()
        return [self._listing(row) for row in rows]
