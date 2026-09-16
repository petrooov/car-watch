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
from dedupe import add_alternative, identity_key, is_probable_duplicate
from validation import apply_review_state


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


def _send_and_log(
    notifier: TelegramNotifier,
    db: Database,
    change: Change,
    recipient_name: str | None = None,
) -> None:
    notifier.send(
        change,
        recipient_name=recipient_name,
        on_result=lambda recipient, status, error: db.log_notification(
            change, recipient, status, error
        ),
    )


def _enrich_and_ai(
    item: Listing,
    all_items: list[Listing],
    scrapers: dict[str, object],
    ranker: AIRanker,
    config: dict,
) -> bool:
    scraper = scrapers.get(item.source)
    if scraper is not None:
        try:
            scraper.enrich(item, max_chars=ranker.max_detail_chars)
        except Exception as exc:
            print(f"[WARN] detail fetch failed for {item.title}: {exc}")

    # The results card may omit fuel while the detail contains it. Re-run all
    # hard filters after enrichment and before spending an AI call or notifying.
    match = evaluate(item, config)
    if not match.accepted:
        print(f"[FILTER] {item.title}: {match.reason}")
        return False

    reasons = apply_review_state(item)
    if reasons:
        print(f"[REVIEW] {item.title}: {'; '.join(reasons)}")
        return False

    if ranker.available:
        ok = ranker.evaluate(item, all_items)
        if ok:
            print(f"[AI] {item.score}/100 · {item.title}")
    return True


def run_once(
    config: dict,
    db: Database,
    notify_existing: bool = True,
    send_all: bool = False,
    dry_run: bool = False,
) -> int:
    timeout = config.get("request_timeout_seconds", 25)
    headers = {"User-Agent": config.get("user_agent", "CarWatch/0.3")}
    notifier = None if dry_run else _telegram(config)
    ranker = AIRanker(config)
    if dry_run:
        ranker.enabled = False

    # Nejprve stáhneme všechny výsledky, aby AI mohla porovnat auto s právě
    # dostupnými kusy stejného modelu.
    accepted_items: dict[str, Listing] = {}
    scraper_instances: dict[str, object] = {}
    searches = [search for search in config.get("searches", []) if search.get("enabled", True)]
    if not dry_run:
        db.migrate_search_state(searches)
    seed_searches = {
        db.search_key(search) for search in searches if not db.search_is_initialized(search)
    }
    successful_searches: dict[str, dict] = {}
    failed_searches: dict[str, tuple[dict, str]] = {}
    item_search_keys: dict[str, str] = {}

    with httpx.Client(timeout=timeout, headers=headers) as client:
        for search in searches:
            source = search["source"]
            search_key = db.search_key(search)
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
                successful_searches[search_key] = search
                print(f"[{source}] {search.get('name', '')}: {len(listings)} listings")
            except Exception as exc:
                failed_searches[search_key] = (search, str(exc))
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
                apply_review_state(item)
                item_search_keys[item.external_id] = search_key

                key = identity_key(item)
                existing = accepted_items.get(key)
                existing_key = key
                if existing is None:
                    duplicate = next(
                        ((candidate_key, candidate) for candidate_key, candidate in accepted_items.items()
                         if is_probable_duplicate(candidate, item)),
                        None,
                    )
                    if duplicate:
                        existing_key, existing = duplicate
                if existing is None or (item.score or 0) > (existing.score or 0):
                    if existing is not None:
                        add_alternative(item, existing)
                        accepted_items.pop(existing_key, None)
                    accepted_items[key] = item
                elif existing is not None:
                    add_alternative(existing, item)

            print(f"[{source}] {search.get('name', '')}: {accepted} matched filters")

        all_items = list(accepted_items.values())
        changes: list[Change] = []

        # Uložíme všechny nalezené kusy a zjistíme, které jsou nové.
        for item in all_items:
            change = db.preview_change(item) if dry_run else db.upsert(item)
            is_seeded = item_search_keys.get(item.external_id) in seed_searches
            if item.review_required:
                print(f"[REVIEW] {item.title}: {item.review_reason}")
                continue
            if change and (dry_run or not (change.kind == "new" and is_seeded)):
                changes.append(change)

        if not dry_run:
            for search_key, search in successful_searches.items():
                db.mark_search_success(search)
            for search_key, (search, error) in failed_searches.items():
                db.mark_search_error(search, error)
            for search_key in sorted(seed_searches & successful_searches.keys()):
                search = successful_searches[search_key]
                seeded = sum(key == search_key for key in item_search_keys.values())
                print(
                    f"[{search['source']}] {search.get('name', '')}: first successful run; "
                    f"seeded {seeded} listings without notifications"
                )

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
            eligible_candidates: list[Listing] = []
            for item in candidates:
                if not _enrich_and_ai(item, all_items, scraper_instances, ranker, config):
                    continue
                eligible_candidates.append(item)
                # Zapiš finální AI score do stávajících DB sloupců score/reason.
                if not dry_run:
                    db.upsert(item)

            top_items = sorted(
                eligible_candidates,
                key=lambda x: x.score or 0,
                reverse=True,
            )[:top_n]

            action = "[DRY-RUN] TOP candidates" if dry_run else "Sending TOP listings to Telegram"
            print(f"\n{action}: {len(top_items)}")
            for i, item in enumerate(top_items, 1):
                print(f"{i}. [{item.score or 0}/100] {item.title} | {item.price or '?'} Kč")
                if notifier:
                    _send_and_log(notifier, db, Change("new", item))
                # Jemné zpomalení kvůli Telegram API při jednorázové dávce.
                if notifier:
                    time.sleep(0.15)
            return len(top_items)

        # Běžný scheduled/--once režim: AI voláme jen pro NOVÉ inzeráty.
        # --seed záměrně AI nepoužívá, aby první inicializace nestála desítky API callů.
        processed = 0
        for change in changes:
            item = change.listing

            if change.kind == "new" and notify_existing:
                if not _enrich_and_ai(item, all_items, scraper_instances, ranker, config):
                    if not dry_run:
                        db.upsert(item)
                    continue
                if not dry_run:
                    db.upsert(item)  # aktualizuje score/reason, nevytvoří další "new"

            processed += 1
            prefix = "[DRY-RUN] " if dry_run else ""
            print("\n" + prefix + format_change(change).replace("<b>", "").replace("</b>", ""))
            if notifier and notify_existing and should_notify(change, config):
                _send_and_log(notifier, db, change)

        return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch car classifieds and notify about new matching ads")
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--db", default="car_watch.sqlite3")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview changes without writing the database, calling AI, or sending Telegram",
    )
    parser.add_argument("--seed", action="store_true", help="populate DB without Telegram notifications")
    parser.add_argument("--test-telegram", action="store_true", help="send one test Telegram message and exit")
    parser.add_argument(
        "--send-all",
        action="store_true",
        help="AI-rank current matches and send TOP N (default 20) to Telegram",
    )
    parser.add_argument(
        "--resend-latest-to",
        metavar="RECIPIENT",
        help="send the latest non-empty database batch only to this Telegram recipient",
    )
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    db = Database(args.db)

    if args.resend_latest_to:
        notifier = _telegram(config, require_enabled=False)
        assert notifier is not None
        items = db.latest_new_listings()
        if not items:
            print("[WARN] Databáze neobsahuje žádné dříve nalezené inzeráty.")
            return
        for item in items:
            _send_and_log(notifier, db, Change("new", item), recipient_name=args.resend_latest_to)
            time.sleep(0.15)
        print(f"[OK] Odesláno {len(items)} inzerátů příjemci '{args.resend_latest_to}'.")
        return

    if args.test_telegram:
        notifier = _telegram(config, require_enabled=False)
        assert notifier is not None
        notifier.send_test()
        print("[OK] Testovací zpráva odeslána všem Telegram příjemcům.")
        return

    if args.send_all:
        run_once(config, db, send_all=True, dry_run=args.dry_run)
        return

    if args.once or args.seed or args.dry_run:
        run_once(config, db, notify_existing=not args.seed, dry_run=args.dry_run)
        return

    interval = max(10, int(config.get("interval_minutes", 30))) * 60
    while True:
        run_once(config, db)
        time.sleep(interval)


if __name__ == "__main__":
    main()
