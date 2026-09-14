from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata

from models import Listing
from utils import detect_fuel


def identity_key(item: Listing) -> str:
    """Nejstabilnější známá identita nabídky."""
    return item.canonical_id or item.external_id


def _norm_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _title_similarity(a: str, b: str) -> float:
    left = _norm_title(a)
    right = _norm_title(b)
    if not left or not right:
        return 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    lt, rt = set(left.split()), set(right.split())
    token_overlap = len(lt & rt) / max(1, len(lt | rt))
    return max(sequence, token_overlap)


def _transmission(value: str | None) -> str | None:
    low = (value or "").lower()
    if "automat" in low:
        return "automatic"
    if "manu" in low:
        return "manual"
    return low or None


def is_probable_duplicate(a: Listing, b: Listing) -> bool:
    """Konzervativní fallback pro stejné auto na dvou různých webech.

    Přesné původní ID nebo VIN mají přednost. Fuzzy shoda se použije
    pouze napříč zdroji a vyžaduje kompletní, velmi podobné údaje.
    """
    if a.external_id == b.external_id:
        return True
    if a.canonical_id and identity_key(a) in {b.external_id, b.canonical_id}:
        return True
    if b.canonical_id and identity_key(b) in {a.external_id, a.canonical_id}:
        return True
    if a.vin and b.vin and a.vin.upper() == b.vin.upper():
        return True
    if a.source == b.source:
        return False

    if not all((a.model, b.model, a.year, b.year, a.mileage_km, b.mileage_km,
                a.price, b.price, a.currency, b.currency)):
        return False
    if a.model != b.model or a.year != b.year or a.currency != b.currency:
        return False
    if abs(a.mileage_km - b.mileage_km) > max(10, round(max(a.mileage_km, b.mileage_km) * 0.001)):
        return False
    if abs(a.price - b.price) / max(a.price, b.price) > 0.15:
        return False
    if a.fuel and b.fuel and detect_fuel(a.fuel) != detect_fuel(b.fuel):
        return False
    if a.transmission and b.transmission and _transmission(a.transmission) != _transmission(b.transmission):
        return False
    return _title_similarity(a.title, b.title) >= 0.78


def add_alternative(primary: Listing, duplicate: Listing) -> None:
    links = list(primary.alternative_urls or [])
    candidates = [(duplicate.source, duplicate.url), *(duplicate.alternative_urls or [])]
    known = {primary.url, *(url for _, url in links)}
    for source, url in candidates:
        if url and url not in known:
            links.append((source, url))
            known.add(url)
    primary.alternative_urls = links or None
