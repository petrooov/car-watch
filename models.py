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

    def as_dict(self) -> dict:
        return asdict(self)
