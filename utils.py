from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse, parse_qs

SPACE_RE = re.compile(r"[\s\u00a0\u202f]+")

# Ordered from the most important/specific category to the broadest. Diesel is
# deliberately first: a diesel hybrid must still be rejected when diesels are
# excluded. Boundaries prevent short badges such as D4 or EV from matching text
# inside ordinary words.
FUEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Diesel", (
        r"\b(?:diesel|naft(?:a|ový|ová|ové|ový motor))\b",
        r"\b(?:t[ -]?di|bi[ -]?tdi|sdi|td[ -]?ci|tddi|d[ -]?ci)\b",
        r"\b(?:blue[ -]?hdi|e[ -]?hdi|hdi|cr[ -]?di|cdi|blue[ -]?tec)\b",
        r"\b(?:cdti|dti|ddi[ -]?s|di[ -]?d|d[ -]?4[ -]?d)\b",
        r"\b(?:i[ -]?dtec|i[ -]?ctdi|skyactiv[ -]?d|eco[ -]?blue)\b",
        r"\b(?:multi[ -]?jet|jtdm?|m[ -]?jet)\b",
        r"\b(?:boxer[ -]?diesel|td4|sd4|edc17)\b",
        r"\bd[2-6]\b",                         # Volvo D2–D6
        r"\b(?:xdrive|sdrive)?\s*\d{2,3}d\b", # BMW/Jaguar 20d, 320d…
        r"\b\d(?:[.,]\d)?\s*d\b",            # 2.0D, 1.6 D
        r"\bd(?:150|180|200|250|300|350)\b",   # modern JLR badges
    )),
    ("Plug-in hybrid", (
        r"\b(?:phev|plug[ -]?in(?: hybrid)?)\b",
    )),
    ("Hybridní", (
        r"\b(?:hybrid(?:ní)?|fhev|mhev|hev|e[ :.-]?hev|e[ -]?power)\b",
    )),
    ("Elektro", (
        r"\b(?:elektro|elektrick(?:ý|á|é)|bev|ev)\b",
    )),
    ("LPG", (r"\blpg\b",)),
    ("CNG", (r"\bcng\b",)),
    ("Benzín", (
        r"\b(?:benz[ií]n|petrol|gasoline)\b",
        r"\b(?:tsi|tfsi|fsi|mpi|gdi|t[ -]?gdi|dig[ -]?t)\b",
        r"\b(?:ecoboost|puretech|thp|tce|boosterjet)\b",
        r"\b(?:skyactiv[ -]?[gx]|vvt[ -]?i)\b",
    )),
)


def clean_text(value: str | None) -> str:
    return SPACE_RE.sub(" ", value or "").strip()


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def parse_mileage_km(text: str | None) -> int | None:
    """Extract a plausible odometer value from Czech classified-ad text.

    Supports separated and compact values (219 000, 219.000, 219000) and
    abbreviated thousands (219 tis. km, 219 tkm). A bare three-digit number is
    intentionally rejected so a broken match can never turn 219,000 into 219.
    """
    # Preserve HTML/block boundaries supplied as newlines. They keep an odometer
    # value on one line from being joined to a price on the next line.
    value = "\n".join(
        cleaned for line in (text or "").splitlines()
        if (cleaned := clean_text(line))
    )
    if not value:
        return None

    label = r"(?:najeto|nájezd|naj\.?|stav\s+tachometru|tachometr)"
    full_number = r"(?:\d{1,3}(?:[ .,  ]\d{3}){1,2}|\d{4,7})"
    thousands = r"(\d{1,3})\s*(?:tis(?:íc)?\.?\s*(?:km)?|t\s*km)\b"

    # Prefer explicitly labelled values, then a conventional "number km" form.
    patterns = (
        (rf"\b{label}\s*[:=.-]?\s*{thousands}", True),
        (rf"\b{thousands}", True),
        (rf"\b{label}\s*[:=.-]?\s*({full_number})(?:\s*km\b)?", False),
        (rf"\b({full_number})\s*km\b", False),
    )
    for pattern, is_thousands in patterns:
        match = re.search(pattern, value, re.I)
        if not match:
            continue
        mileage = int(match.group(1)) * 1_000 if is_thousands else parse_int(match.group(1))
        if mileage is not None and 1_000 <= mileage <= 2_000_000:
            return mileage
    return None


def detect_fuel(text: str | None) -> str | None:
    """Infer a normalized fuel category from listing text or an engine badge."""
    value = clean_text(text)
    if not value:
        return None
    for fuel, patterns in FUEL_PATTERNS:
        if any(re.search(pattern, value, re.I) for pattern in patterns):
            return fuel
    return None


def stable_id(source: str, url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("id", "adId", "ref"):
        if qs.get(key):
            return f"{source}:{qs[key][0]}"
    path_id = re.search(r"(?:^|[-_/])(\d{5,})(?:$|[/?#.-])", parsed.path)
    if path_id:
        return f"{source}:{path_id.group(1)}"
    return f"{source}:{hashlib.sha1(url.encode()).hexdigest()[:18]}"


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)
