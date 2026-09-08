from __future__ import annotations

import html
import os
import httpx

from db import Change


def format_change(change: Change) -> str:
    x = change.listing
    source_names = {"sauto": "Sauto", "tipcars": "TipCars", "bazos_auto": "Bazoš Auto", "mobile_de": "mobile.de"}
    kind = "🆕 NOVÝ INZERÁT" if change.kind == "new" else "💸 ZMĚNA CENY"
    parts = [f"<b>{kind}</b> · {source_names.get(x.source, x.source)}", f"<b>{html.escape(x.title)}</b>"]
    if x.score is not None:
        label = "🔥" if x.score >= 90 else "✅" if x.score >= 80 else "🔎"
        model = f" · {html.escape(x.model)}" if x.model else ""
        parts.append(f"{label} <b>{x.score}/100</b>{model}")
    details = []
    if x.year:
        details.append(str(x.year))
    if x.mileage_km is not None:
        details.append(f"{x.mileage_km:,} km".replace(",", " "))
    if x.fuel:
        details.append(x.fuel)
    if x.transmission:
        details.append(x.transmission)
    if details:
        parts.append(" · ".join(map(html.escape, details)))
    if x.match_reason and x.match_reason != "ideální rozsah":
        parts.append(html.escape(x.match_reason))

    if x.price is not None:
        currency = x.currency or ""
        price = f"{x.price:,}".replace(",", " ")
        if change.kind == "price_change" and change.old_price is not None:
            old = f"{change.old_price:,}".replace(",", " ")
            parts.append(f"💰 <b>{old} → {price} {currency}</b>")
        else:
            parts.append(f"💰 <b>{price} {currency}</b>")

    parts.append(f'<a href="{html.escape(x.url, quote=True)}">Otevřít inzerát</a>')
    return "\n".join(parts)


class TelegramNotifier:
    def __init__(self, bot_token_env: str = "TELEGRAM_BOT_TOKEN", chat_id_env: str = "TELEGRAM_CHAT_ID"):
        self.token = os.getenv(bot_token_env)
        self.chat_id = os.getenv(chat_id_env)
        if not self.token or not self.chat_id:
            raise RuntimeError(
                f"Chybí {bot_token_env} nebo {chat_id_env}. "
                "Zkopíruj .env.example na .env a doplň obě hodnoty."
            )

    def _post(self, payload: dict) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        r = httpx.post(url, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data

    def send(self, change: Change) -> None:
        self._post({
            "chat_id": self.chat_id,
            "text": format_change(change),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        })

    def send_test(self) -> None:
        self._post({
            "chat_id": self.chat_id,
            "text": "✅ CarWatch je připojený. Telegram upozornění fungují.",
        })
