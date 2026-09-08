from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models import Listing


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
        """)
        # Lightweight migration for databases created by older versions.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(listings)")}
        for name, decl in (("model", "TEXT"), ("score", "INTEGER"), ("match_reason", "TEXT")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE listings ADD COLUMN {name} {decl}")
        self.conn.commit()

    def upsert(self, item: Listing) -> Change | None:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT price FROM listings WHERE external_id = ?", (item.external_id,)
        ).fetchone()

        if row is None:
            self.conn.execute("""
                INSERT INTO listings (
                    external_id, source, url, title, price, currency, year,
                    mileage_km, fuel, transmission, location, image_url, model, score, match_reason,
                    first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.external_id, item.source, item.url, item.title, item.price,
                item.currency, item.year, item.mileage_km, item.fuel,
                item.transmission, item.location, item.image_url, item.model, item.score, item.match_reason, now, now
            ))
            self.conn.execute(
                "INSERT INTO price_history(external_id, price, seen_at) VALUES (?, ?, ?)",
                (item.external_id, item.price, now),
            )
            self.conn.commit()
            return Change("new", item)

        old_price = row["price"]
        self.conn.execute("""
            UPDATE listings SET
                url=?, title=?, price=?, currency=?, year=?, mileage_km=?, fuel=?,
                transmission=?, location=?, image_url=?, model=?, score=?, match_reason=?, last_seen=?
            WHERE external_id=?
        """, (
            item.url, item.title, item.price, item.currency, item.year,
            item.mileage_km, item.fuel, item.transmission, item.location,
            item.image_url, item.model, item.score, item.match_reason, now, item.external_id
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
