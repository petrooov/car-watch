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
        CREATE TABLE IF NOT EXISTS search_state (
            search_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            name TEXT NOT NULL,
            initialized_at TEXT NOT NULL,
            last_success_at TEXT,
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL,
            change_kind TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            sent_at TEXT,
            error TEXT
        );
        """)
        # Lightweight migration for databases created by older versions.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(listings)")}
        for name, decl in (
            ("model", "TEXT"), ("score", "INTEGER"), ("match_reason", "TEXT"),
            ("canonical_id", "TEXT"), ("vin", "TEXT"), ("alternative_urls", "TEXT"),
            ("review_required", "INTEGER NOT NULL DEFAULT 0"), ("review_reason", "TEXT"),
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

    @staticmethod
    def search_key(search: dict) -> str:
        return f"{search.get('source', '')}:{search.get('name', '')}"

    def migrate_search_state(self, searches: list[dict]) -> None:
        """Map legacy source-level initialization to current searches once."""
        if self.conn.execute("SELECT 1 FROM search_state LIMIT 1").fetchone():
            return
        initialized_sources = {
            row["source"] for row in self.conn.execute("SELECT source FROM source_state")
        }
        if not initialized_sources:
            return
        now = datetime.now(timezone.utc).isoformat()
        for search in searches:
            if search.get("source") in initialized_sources:
                self.conn.execute(
                    """INSERT OR IGNORE INTO search_state
                       (search_key, source, name, initialized_at, last_success_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (self.search_key(search), search["source"], search.get("name", ""), now, now),
                )
        self.conn.commit()

    def search_is_initialized(self, search: dict) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM search_state WHERE search_key = ?", (self.search_key(search),)
        ).fetchone() is not None

    def mark_search_success(self, search: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO search_state
               (search_key, source, name, initialized_at, last_success_at, last_error)
               VALUES (?, ?, ?, ?, ?, NULL)
               ON CONFLICT(search_key) DO UPDATE SET last_success_at=excluded.last_success_at,
                   last_error=NULL""",
            (self.search_key(search), search["source"], search.get("name", ""), now, now),
        )
        self.conn.commit()

    def mark_search_error(self, search: dict, error: str) -> None:
        row = self.conn.execute(
            "SELECT 1 FROM search_state WHERE search_key = ?", (self.search_key(search),)
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE search_state SET last_error = ? WHERE search_key = ?",
                (error[:1000], self.search_key(search)),
            )
            self.conn.commit()

    def log_notification(
        self,
        change: Change,
        recipient: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO notification_log
               (external_id, change_kind, recipient, status, attempted_at, sent_at, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (change.listing.external_id, change.kind, recipient, status, now,
             now if status == "sent" else None, error[:2000] if error else None),
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
                    canonical_id, vin, alternative_urls, review_required, review_reason,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.external_id, item.source, item.url, item.title, item.price,
                item.currency, item.year, item.mileage_km, item.fuel,
                item.transmission, item.location, item.image_url, item.model, item.score,
                item.match_reason, item.canonical_id, item.vin,
                json.dumps(item.alternative_urls or [], ensure_ascii=False),
                int(item.review_required), item.review_reason, now, now
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
                canonical_id=?, vin=?, alternative_urls=?, review_required=?, review_reason=?,
                last_seen=?
            WHERE external_id=?
        """, (
            item.url, item.title, item.price, item.currency, item.year,
            item.mileage_km, item.fuel, item.transmission, item.location,
            item.image_url, item.model, item.score, item.match_reason,
            item.canonical_id, item.vin,
            json.dumps(item.alternative_urls or [], ensure_ascii=False),
            int(item.review_required), item.review_reason, now, item.external_id
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
            review_required=bool(row["review_required"]), review_reason=row["review_reason"],
        )

    def preview_change(self, item: Listing) -> Change | None:
        """Classify an item without changing listings, history, or timestamps."""
        row = self.conn.execute(
            "SELECT * FROM listings WHERE external_id = ?", (item.external_id,)
        ).fetchone()
        if row is None and item.canonical_id:
            row = self.conn.execute(
                "SELECT * FROM listings WHERE external_id = ? OR canonical_id = ? LIMIT 1",
                (item.canonical_id, item.canonical_id),
            ).fetchone()
        if row is None and item.vin:
            row = self.conn.execute("SELECT * FROM listings WHERE vin = ? LIMIT 1", (item.vin,)).fetchone()
        if row is None and item.model and item.year and item.mileage_km is not None:
            tolerance = max(10, round(item.mileage_km * 0.001))
            candidates = self.conn.execute(
                """SELECT * FROM listings
                   WHERE source != ? AND model = ? AND year = ?
                     AND mileage_km BETWEEN ? AND ?""",
                (item.source, item.model, item.year,
                 item.mileage_km - tolerance, item.mileage_km + tolerance),
            ).fetchall()
            row = next(
                (candidate for candidate in candidates
                 if is_probable_duplicate(self._listing(candidate), item)),
                None,
            )
        if row is None:
            return Change("new", item)
        if row["external_id"] != item.external_id:
            return None
        if item.price is not None and row["price"] != item.price:
            return Change("price_change", item, row["price"])
        return None

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
