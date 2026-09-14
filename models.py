from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(slots=True)
class Listing:
    source: str
    external_id: str
    url: str
    title: str
    price: Optional[int] = None
    currency: Optional[str] = None
    year: Optional[int] = None
    mileage_km: Optional[int] = None
    fuel: Optional[str] = None
    transmission: Optional[str] = None
    location: Optional[str] = None
    image_url: Optional[str] = None
    model: Optional[str] = None
    score: Optional[int] = None
    match_reason: Optional[str] = None

    # Detail inzerátu – používá se pro AI hodnocení, do SQLite se neukládá.
    detail_text: Optional[str] = None
    equipment_text: Optional[str] = None

    # Strukturovaný výstup AI – používá se hlavně v Telegram notifikaci.
    ai_verdict: Optional[str] = None
    ai_summary: Optional[str] = None
    ai_value_score: Optional[int] = None
    ai_equipment_score: Optional[int] = None
    ai_reliability_score: Optional[int] = None
    ai_positives: Optional[list[str]] = None
    ai_warnings: Optional[list[str]] = None

    # Identita fyzické nabídky napříč agregátory. Carvago například
    # zveřejňuje ID původního inzerátu z mobile.de. Alternativní odkazy se
    # zobrazí v jedné notifikaci místo odeslání duplikátu.
    canonical_id: Optional[str] = None
    vin: Optional[str] = None
    alternative_urls: Optional[list[tuple[str, str]]] = None

    def as_dict(self) -> dict:
        return asdict(self)
