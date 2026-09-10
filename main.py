from __future__ import annotations

import argparse
import time

import httpx
import yaml
from dotenv import load_dotenv

from ai_ranker import AIRanker
from db import Change, Database
from notifier import TelegramNotifier, format_change
from matcher import evaluate
from models import Listing
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


def _telegram(config: dict, require_enabled: bool = True) -> TelegramNotifier | None:
    telegram_cfg = config.get("telegram", {})
    if require_enabled and not telegram_cfg.get("enabled"):
        return None
    return TelegramNotifier(
        telegram_cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
        telegram_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"),
        telegram_cfg.get("recipients"),
    )


def _enrich_and_ai(
    item: Listing,
    all_items: list[Listing],
    scrapers: dict[str, object],
    ranker: AIRanker,
) -> None:
    scraper = scrapers.get(item.source)
    if scraper is not None:
        try:
            scraper.enrich(item, max_chars=ranker.max_detail_chars)
        except Exception as exc:
            print(f"[WARN] detail fetch failed for {item.title}: {exc}")

    if ranker.available:
        ok = ranker.evaluate(item, all_items)
        if ok:
            print(f"[AI] {item.score}/100 · {item.title}")


def run_once(
    config: dict,
    db: Database,
    notify_existing: bool = True,
    send_all: bool = False,
) -> int:
    timeout = config.get("request_timeout_seconds", 25)
    headers = {"User-Agent": config.get("user_agent", "CarWatch/0.3")}
    notifier = _telegram(config)
    ranker = AIRanker(config)

    # Nejprve stáhneme všechny výsledky, aby AI mohla porovnat auto s právě
    # dostupnými kusy stejného modelu.
    accepted_items: dict[str, Listing] = {}
    scraper_instances: dict[str, object] = {}

    with httpx.Client(timeout=timeout, headers=headers) as client:
        for search in config.get("searches", []):
            if not search.get("enabled", True):
                continue

            source = search["source"]
            scraper_cls = SCRAPERS.get(source)
            if not scraper_cls:
                print(f"[WARN] Unknown source: {source}")
                continue

            scraper = scraper_instances.get(source)
            if scraper is None:
                scraper = scraper_cls(client)
                scraper_instances[source] = scraper

            try:
                listings = scraper.scrape(search["url"])
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

                key = f"{item.source}:{item.external_id}"
                existing = accepted_items.get(key)
                if existing is None or (item.score or 0) > (existing.score or 0):
                    accepted_items[key] = item

            print(f"[{source}] {search.get('name', '')}: {accepted} matched filters")

        all_items = list(accepted_items.values())
        changes: list[Change] = []

        # Uložíme všechny nalezené kusy a zjistíme, které jsou nové.
        for item in all_items:
            change = db.upsert(item)
            if change:
                changes.append(change)

        if send_all:
            ai_cfg = config.get("ai", {})
            candidate_limit = max(20, int(ai_cfg.get("send_all_candidate_limit", 50)))
            top_n = max(1, int(ai_cfg.get("send_all_top_n", 20)))

            # Předvýběr levným lokálním score, pak detail + AI.
            candidates = sorted(
                all_items,
                key=lambda x: x.score or 0,
                reverse=True,
            )[:candidate_limit]

            print(f"\n[send-all] AI/detail shortlist: {len(candidates)} aut")
            for item in candidates:
                _enrich_and_ai(item, all_items, scraper_instances, ranker)
                # Zapiš finální AI score do stávajících DB sloupců score/reason.
                db.upsert(item)

            top_items = sorted(
                candidates,
                key=lambda x: x.score or 0,
                reverse=True,
            )[:top_n]

            print(f"\nSending TOP {len(top_items)} listings to Telegram:")
            for i, item in enumerate(top_items, 1):
                print(f"{i}. [{item.score or 0}/100] {item.title} | {item.price or '?'} Kč")
                if notifier:
                    notifier.send(Change("new", item))
                # Jemné zpomalení kvůli Telegram API při jednorázové dávce.
                time.sleep(0.15)
            return len(top_items)

        # Běžný scheduled/--once režim: AI voláme jen pro NOVÉ inzeráty.
        # --seed záměrně AI nepoužívá, aby první inicializace nestála desítky API callů.
        processed = 0
        for change in changes:
            item = change.listing

            if change.kind == "new" and notify_existing:
                _enrich_and_ai(item, all_items, scraper_instances, ranker)
                db.upsert(item)  # aktualizuje score/reason, nevytvoří další "new"

            processed += 1
            print("\n" + format_change(change).replace("<b>", "").replace("</b>", ""))
            if notifier and notify_existing and should_notify(change, config):
                notifier.send(change)

        return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch car classifieds and notify about new matching ads")
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--db", default="car_watch.sqlite3")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--seed", action="store_true", help="populate DB without Telegram notifications")
    parser.add_argument("--test-telegram", action="store_true", help="send one test Telegram message and exit")
    parser.add_argument(
        "--send-all",
        action="store_true",
        help="AI-rank current matches and send TOP N (default 20) to Telegram",
    )
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    db = Database(args.db)

    if args.test_telegram:
        notifier = _telegram(config, require_enabled=False)
        assert notifier is not None
        notifier.send_test()
        print("[OK] Testovací zpráva odeslána všem Telegram příjemcům.")
        return

    if args.send_all:
        run_once(config, db, send_all=True)
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
