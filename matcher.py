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


def evaluate(item: Listing, cfg: dict) -> MatchResult:
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

    # Start high and apply gentle penalties outside the ideal window.
    score = 100
    reasons: list[str] = []

    if item.year is None:
        score -= 12
        reasons.append("rok neznámý")
    elif item.year < ideal_year:
        penalty = min(18, (ideal_year - item.year) * 8)
        score -= penalty
        reasons.append(f"rok {item.year}")
    elif item.year >= ideal_year + 2:
        score += 3

    if item.mileage_km is None:
        score -= 12
        reasons.append("nájezd neznámý")
    elif item.mileage_km > ideal_mileage:
        over = item.mileage_km - ideal_mileage
        penalty = min(24, round(over / 5_000) * 3)
        score -= penalty
        reasons.append(f"{item.mileage_km // 1000} tis. km")
    elif item.mileage_km <= 80_000:
        score += 5

    if item.price is None:
        score -= 15
        reasons.append("cena neznámá")
    elif item.price > ideal_price:
        over = item.price - ideal_price
        score -= min(15, round(over / 10_000) * 2)
    elif item.price <= 475_000:
        score += 5

    # Optional per-model preference bonus, e.g. for RAV4/CR-V/Outlander.
    score += int(rule.get("bonus", 0))
    score = max(0, min(100, score))

    return MatchResult(True, score=score, model=model, reason=", ".join(reasons) or "ideální rozsah")
