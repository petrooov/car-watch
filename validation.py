from __future__ import annotations

import re
from datetime import datetime, timezone

from models import Listing
from utils import detect_fuel, parse_int


def review_reasons(item: Listing, current_year: int | None = None) -> list[str]:
    """Return data conflicts serious enough to suppress automatic notification."""
    year_now = current_year or datetime.now(timezone.utc).year
    reasons: list[str] = []

    if item.year is not None and not 1980 <= item.year <= year_now + 1:
        reasons.append(f"podezřelý rok {item.year}")
    if item.mileage_km is not None and not 0 <= item.mileage_km <= 2_000_000:
        reasons.append(f"podezřelý nájezd {item.mileage_km} km")
    elif (
        item.mileage_km is not None
        and item.mileage_km < 1_000
        and item.year is not None
        and item.year <= year_now - 2
    ):
        reasons.append(
            f"neobvykle nízký nájezd {item.mileage_km} km pro rok {item.year}"
        )

    title_fuel = detect_fuel(item.title)
    if item.fuel and title_fuel:
        explicit = item.fuel.lower()
        title_kind = title_fuel.lower()
        diesel_conflict = ("diesel" in explicit or "nafta" in explicit) != (title_kind == "diesel")
        if diesel_conflict:
            reasons.append(f"rozpor paliva: pole {item.fuel}, název {title_fuel}")

    title_transmission = _title_transmission(item.title)
    if item.transmission and title_transmission:
        explicit_manual = "manu" in item.transmission.lower()
        title_manual = title_transmission == "manuální"
        if explicit_manual != title_manual:
            reasons.append(
                f"rozpor převodovky: pole {item.transmission}, název {title_transmission}"
            )

    detail = item.detail_text or ""
    if detail:
        detail_year = _labelled_year(detail)
        if item.year is not None and detail_year is not None and detail_year != item.year:
            reasons.append(f"rozpor roku: seznam {item.year}, detail {detail_year}")

        detail_mileage = _labelled_mileage(detail)
        if item.mileage_km is not None and detail_mileage is not None:
            tolerance = max(1_000, round(max(item.mileage_km, detail_mileage) * 0.05))
            if abs(detail_mileage - item.mileage_km) > tolerance:
                reasons.append(
                    f"rozpor nájezdu: seznam {item.mileage_km} km, detail {detail_mileage} km"
                )

    return reasons


def apply_review_state(item: Listing, current_year: int | None = None) -> list[str]:
    reasons = review_reasons(item, current_year=current_year)
    item.review_required = bool(reasons)
    item.review_reason = "; ".join(reasons) if reasons else None
    return reasons


def _labelled_year(text: str) -> int | None:
    match = re.search(
        r"(?:rok\s+výroby|vyrobeno|první\s+registrace|r\.?\s*v\.?)\s*[:=.-]?\s*"
        r"(?:\d{1,2}/)?(19\d{2}|20\d{2})\b",
        text,
        re.I,
    )
    return int(match.group(1)) if match else None


def _labelled_mileage(text: str) -> int | None:
    match = re.search(
        r"(?:najeto|nájezd|stav\s+tachometru|tachometr)\s*[:=.-]?\s*"
        r"(\d{1,3}(?:[ .\u00a0\u202f]\d{3}){1,2}|\d{4,7})\s*km\b",
        text,
        re.I,
    )
    return parse_int(match.group(1)) if match else None


def _title_transmission(title: str) -> str | None:
    if re.search(r"\b(?:manu[aá]l(?:ní)?|manual|6mt|5mt)\b", title, re.I):
        return "manuální"
    if re.search(r"\b(?:automat(?:ická|ický)?|dsg|cvt|dct|at)\b", title, re.I):
        return "automatická"
    return None
