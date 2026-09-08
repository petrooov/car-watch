from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse, parse_qs

SPACE_RE = re.compile(r"[\s\u00a0\u202f]+")


def clean_text(value: str | None) -> str:
    return SPACE_RE.sub(" ", value or "").strip()


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


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
