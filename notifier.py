from __future__ import annotations

import html
import os
from collections.abc import Callable
import httpx

from db import Change


def format_change(change: Change) -> str:
    x = change.listing
    source_names = {
        "sauto": "Sauto",
        "tipcars": "TipCars",
        "bazos_auto": "Bazoš Auto",
        "mobile_de": "mobile.de",
        "carvago": "Carvago",
    }
    kind = "🆕 NOVÝ INZERÁT" if change.kind == "new" else "💸 ZMĚNA CENY"
    parts = [
        f"<b>{kind}</b> · {source_names.get(x.source, x.source)}",
        f"<b>{html.escape(x.title)}</b>",
    ]

    if x.score is not None:
        label = "🔥" if x.score >= 90 else "✅" if x.score >= 80 else "🔎"
        model = f" · {html.escape(x.model)}" if x.model else ""
        ai = " · AI" if x.ai_summary else ""
        parts.append(f"{label} <b>{x.score}/100</b>{model}{ai}")

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

    if x.ai_summary:
        parts.append(f"🤖 {html.escape(x.ai_summary)}")
        breakdown = []
        if x.ai_value_score is not None:
            breakdown.append(f"hodnota {x.ai_value_score}")
        if x.ai_equipment_score is not None:
            breakdown.append(f"výbava {x.ai_equipment_score}")
        if x.ai_reliability_score is not None:
            breakdown.append(f"spolehlivost {x.ai_reliability_score}")
        if breakdown:
            parts.append(" · ".join(breakdown))

        for positive in (x.ai_positives or [])[:3]:
            parts.append(f"+ {html.escape(positive)}")
        for warning in (x.ai_warnings or [])[:3]:
            parts.append(f"⚠️ {html.escape(warning)}")
    elif x.match_reason and x.match_reason != "ideální rozsah":
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
    for source, url in (x.alternative_urls or []):
        label = source_names.get(source, source)
        parts.append(
            f'<a href="{html.escape(url, quote=True)}">Také na {html.escape(label)}</a>'
        )
    return "\n".join(parts)


class TelegramNotifier:
    def __init__(
        self,
        bot_token_env: str = "TELEGRAM_BOT_TOKEN",
        chat_id_env: str = "TELEGRAM_CHAT_ID",
        recipients: list[dict] | None = None,
    ):
        recipient_configs = recipients or [{
            "name": "hlavni",
            "bot_token_env": bot_token_env,
            "chat_id_env": chat_id_env,
        }]
        self.recipients: list[tuple[str, str, str]] = []

        for recipient in recipient_configs:
            if not recipient.get("enabled", True):
                continue

            name = recipient.get("name", "telegram")
            token_env = recipient.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
            chat_env = recipient.get("chat_id_env", "TELEGRAM_CHAT_ID")
            token = os.getenv(token_env)
            chat_id = os.getenv(chat_env)

            # Volitelný druhý příjemce se aktivuje až doplněním obou hodnot.
            if recipient.get("optional", False) and not token and not chat_id:
                continue
            if not token or not chat_id:
                raise RuntimeError(
                    f"Příjemce '{name}': chybí {token_env} nebo {chat_env}. "
                    "Doplň obě hodnoty do .env / GitHub Actions secrets."
                )
            self.recipients.append((token, chat_id, name))

        if not self.recipients:
            raise RuntimeError("Není nakonfigurovaný žádný Telegram příjemce.")

    def _post(self, token: str, payload: dict) -> dict:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = httpx.post(url, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data

    def send(
        self,
        change: Change,
        recipient_name: str | None = None,
        on_result: Callable[[str, str, str | None], None] | None = None,
    ) -> list[str]:
        recipients = [
            recipient for recipient in self.recipients
            if recipient_name is None or recipient[2] == recipient_name
        ]
        if not recipients:
            raise RuntimeError(f"Telegram příjemce '{recipient_name}' není aktivní nebo nemá vyplněné údaje.")

        text = format_change(change)
        sent: list[str] = []
        for token, chat_id, name in recipients:
            try:
                self._post(token, {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                })
            except Exception as exc:
                if on_result:
                    on_result(name, "failed", str(exc))
                raise
            sent.append(name)
            if on_result:
                on_result(name, "sent", None)
        return sent

    def send_test(self, recipient_name: str | None = None) -> None:
        recipients = [
            recipient for recipient in self.recipients
            if recipient_name is None or recipient[2] == recipient_name
        ]
        if not recipients:
            raise RuntimeError(f"Telegram příjemce '{recipient_name}' není aktivní nebo nemá vyplněné údaje.")

        for token, chat_id, name in recipients:
            self._post(token, {
                "chat_id": chat_id,
                "text": f"✅ CarWatch je připojený ({name}). Telegram upozornění fungují.",
            })
