from __future__ import annotations

from dataclasses import dataclass
from models import Listing


@dataclass(slots=True)
class MatchResult:
    accepted: bool
    score: int = 0
    model: str | None = None
    reason: str | None = None


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())


def _find_model(title: str, rules: dict) -> tuple[str | None, dict | None]:
    hay = _norm(title)
    for model, rule in rules.items():
        aliases = [model, *rule.get("aliases", [])]
        if any(_norm(alias) in hay for alias in aliases):
            return model, rule
    return None, None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def evaluate(item: Listing, cfg: dict) -> MatchResult:
    """Hard filter + levné předběžné score.

    Finální score může později přepsat AI. Toto score se používá jako fallback a
    pro zúžení kandidátů při --send-all, aby nebylo nutné posílat stovky aut do API.
    """
    rules = cfg.get("vehicle_rules", {})
    model, rule = _find_model(item.title, rules)
    if rules and not rule:
        return MatchResult(False, reason="model not on whitelist")

    rule = rule or {}
    defaults = cfg.get("filters", {})

    max_price = int(rule.get("max_price", defaults.get("max_price", 550_000)))
    min_year = int(rule.get("min_year", defaults.get("min_year", 2019)))
    max_mileage = int(rule.get("max_mileage_km", defaults.get("max_mileage_km", 130_000)))
    ideal_year = int(rule.get("ideal_year", defaults.get("ideal_year", 2020)))
    ideal_mileage = int(rule.get("ideal_mileage_km", defaults.get("ideal_mileage_km", 100_000)))
    ideal_price = int(rule.get("ideal_price", defaults.get("ideal_price", 500_000)))

    if item.currency not in (None, "CZK"):
        return MatchResult(False, model=model, reason=f"unsupported currency {item.currency}")
    if item.price is not None and item.price > max_price:
        return MatchResult(False, model=model, reason="over max price")
    if item.year is not None and item.year < min_year:
        return MatchResult(False, model=model, reason="too old")
    if item.mileage_km is not None and item.mileage_km > max_mileage:
        return MatchResult(False, model=model, reason="too many km")

    reasons: list[str] = []

    # Předběžné score je schválně rozprostřené a nemá snadno saturovat na 100.
    # Rok: 0–30
    if item.year is None:
        year_score = 10.0
        reasons.append("rok neznámý")
    else:
        year_score = 18 + (item.year - ideal_year) * 4
        year_score = _clamp(year_score, 0, 30)
        if item.year < ideal_year:
            reasons.append(f"rok {item.year}")

    # Nájezd: 0–30
    if item.mileage_km is None:
        mileage_score = 9.0
        reasons.append("nájezd neznámý")
    else:
        mileage_score = 20 + ((ideal_mileage - item.mileage_km) / 10_000) * 2.5
        mileage_score = _clamp(mileage_score, 0, 30)
        if item.mileage_km > ideal_mileage:
            reasons.append(f"{item.mileage_km // 1000} tis. km")

    # Cena: 0–30. Jen předfiltr; AI dostane navíc mediány stejného modelu.
    if item.price is None:
        price_score = 8.0
        reasons.append("cena neznámá")
    else:
        price_score = 20 + ((ideal_price - item.price) / 10_000) * 1.0
        price_score = _clamp(price_score, 0, 30)

    # Kompletnost dat: 0–5
    completeness = sum(
        value is not None
        for value in (item.price, item.year, item.mileage_km, item.fuel, item.transmission)
    )
    completeness_score = completeness

    # Preference modelu z configu: typicky 0–4 body.
    model_bonus = int(rule.get("bonus", 0))

    score = round(year_score + mileage_score + price_score + completeness_score + model_bonus)
    score = max(0, min(99, score))  # 100 si necháváme pro opravdu výjimečný AI verdikt.

    return MatchResult(
        True,
        score=score,
        model=model,
        reason=", ".join(reasons) or "ideální rozsah",
    )
