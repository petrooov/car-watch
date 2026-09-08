from __future__ import annotations

import argparse
import time
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

from db import Database
from notifier import TelegramNotifier, format_change
from matcher import evaluate
from scrapers import SCRAPERS


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def should_notify(change, config: dict) -> bool:
    notifications = config.get("notifications", {})
    if change.kind == "new":
        return notifications.get("new_listings", True)
    if change.kind == "price_change":
        return notifications.get("price_changes", False)
    return False


def run_once(config: dict, db: Database, notify_existing: bool = True) -> int:
    timeout = config.get("request_timeout_seconds", 25)
    headers = {"User-Agent": config.get("user_agent", "CarWatch/0.1")}
    telegram_cfg = config.get("telegram", {})
    notifier = None
    if telegram_cfg.get("enabled"):
        notifier = TelegramNotifier(
            telegram_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
            telegram_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"),
        )

    changes = 0
    with httpx.Client(timeout=timeout, headers=headers) as client:
        for search in config.get("searches", []):
            if not search.get("enabled", True):
                continue
            source = search["source"]
            scraper_cls = SCRAPERS.get(source)
            if not scraper_cls:
                print(f"[WARN] Unknown source: {source}")
                continue
            try:
                listings = scraper_cls(client).scrape(search["url"])
                print(f"[{source}] {search.get('name', '')}: {len(listings)} listings")
            except Exception as exc:
                print(f"[ERROR] {source}: {exc}")
                continue

            accepted = 0
            for item in listings:
                match = evaluate(item, config)
                if not match.accepted:
                    continue
                accepted += 1
                item.model = match.model
                item.score = match.score
                item.match_reason = match.reason
                change = db.upsert(item)
                if not change:
                    continue
                changes += 1
                print("\n" + format_change(change).replace("<b>", "").replace("</b>", ""))
                if notifier and notify_existing and should_notify(change, config):
                    notifier.send(change)
            print(f"[{source}] {search.get('name', '')}: {accepted} matched filters")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch car classifieds and notify about new matching ads")
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--db", default="car_watch.sqlite3")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--seed", action="store_true", help="populate DB without Telegram notifications")
    parser.add_argument("--test-telegram", action="store_true", help="send one test Telegram message and exit")
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    db = Database(args.db)

    if args.test_telegram:
        telegram_cfg = config.get("telegram", {})
        TelegramNotifier(
            telegram_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
            telegram_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"),
        ).send_test()
        print("[OK] Testovací zpráva odeslána na Telegram.")
        return

    if args.once or args.seed:
        run_once(config, db, notify_existing=not args.seed)
        return

    interval = max(10, int(config.get("interval_minutes", 30))) * 60
    while True:
        run_once(config, db)
        time.sleep(interval)


if __name__ == "__main__":
    main()
