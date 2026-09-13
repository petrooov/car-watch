from __future__ import annotations

import json
import os
from statistics import median

import httpx

from models import Listing


EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "value_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "equipment_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reliability_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string"},
        "summary": {"type": "string"},
        "positives": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
    "required": [
        "score", "value_score", "equipment_score", "reliability_score",
        "verdict", "summary", "positives", "warnings",
    ],
    "additionalProperties": False,
}


class AIRanker:
    def __init__(self, cfg: dict):
        ai_cfg = cfg.get("ai", {})
        self.enabled = bool(ai_cfg.get("enabled", False))
        self.model = ai_cfg.get("model", "gpt-5-mini")
        self.api_key_env = ai_cfg.get("api_key_env", "OPENAI_API_KEY")
        self.max_detail_chars = int(ai_cfg.get("max_detail_chars", 14_000))
        self.api_key = os.getenv(self.api_key_env)
        if self.enabled and not self.api_key:
            print(f"[WARN] AI enabled, but {self.api_key_env} is missing. Using fallback score.")

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.api_key)

    @staticmethod
    def peer_context(item: Listing, peers: list[Listing]) -> str:
        same = [x for x in peers if x.model == item.model]
        prices = [x.price for x in same if x.price is not None]
        mileages = [x.mileage_km for x in same if x.mileage_km is not None]
        years = [x.year for x in same if x.year is not None]

        lines = [f"Počet aktuálních odpovídajících nabídek stejného modelu: {len(same)}"]
        if prices:
            lines.append(f"Medián ceny stejného modelu: {round(median(prices)):,} Kč".replace(",", " "))
        if mileages:
            lines.append(f"Medián nájezdu: {round(median(mileages)):,} km".replace(",", " "))
        if years:
            lines.append(f"Medián roku: {median(years):g}")
        return "\n".join(lines)

    def evaluate(self, item: Listing, peers: list[Listing]) -> bool:
        if not self.available:
            return False

        detail = (item.detail_text or "")[: self.max_detail_chars]
        peer_context = self.peer_context(item, peers)

        prompt = f"""
Ohodnoť tento konkrétní inzerát na ojeté SUV pro českého kupujícího.

Profil kupujícího:
- maximální rozpočet 550 000 Kč
- chce SUV zhruba velikosti Toyota RAV4
- roční nájezd přibližně 20 000 km, kombinace město + dálnice
- důležité jsou spolehlivost, rozumné provozní náklady a praktičnost
- stačí běžná komfortní výbava a připojení telefonu (např. Apple CarPlay);
  luxusní prvky ani nejvyšší výbavový stupeň nejsou prioritou
- rok ideálně 2020+, nájezd ideálně kolem 100 000 km nebo méně

Pravidla hodnocení:
- SCORE 0–100 je celková atraktivita koupě, ne jen shoda s filtrem.
- Zohledni cenu, rok, nájezd, konkrétní motor/pohon, známá rizika dané verze,
  servisní informace, původ, výbavu a kvalitu/důvěryhodnost inzerátu.
- U výbavy mají přednost konkrétní údaje z inzerátu. Pokud seznam chybí nebo je
  neúplný, zohledni obvyklou úroveň výbavy podle známého výbavového stupně
  v titulku či popisu (např. Elegance), v kontextu modelu a přibližného ročníku.
  Samotný název stupně není důkazem konkrétního prvku; odhad úrovně výbavy
  neprezentuj jako potvrzený seznam a nepřipisuj vozu příplatkové prvky.
- Chybějící seznam výbavy nesnižuje equipment_score ani celkové score a sám
  o sobě není důvodem pro warnings ani pro snížení důvěryhodnosti inzerátu.
  Pokud neznáš ani výbavový stupeň, hodnoť výbavu neutrálně.
- Běžná výbava potřebám kupujícího stačí. Nižší hodnocení výbavy použij zejména
  u rozpoznané nejnižší/základní verze s opravdu chudou výbavou nebo při výslovně
  uvedené absenci důležitého prvku. Luxusní prvky mají jen malou váhu.
- U známých technických rizik buď opatrný; pokud přesnou motorizaci neznáš, napiš to do warnings.
- Peer statistiky níže jsou aktuální data z právě stažených inzerátů, použij je pro value_score.
- Nezvyšuj score jen proto, že jde o prémiovou značku.
- Chybějící informace mimo výbavu jsou mírné riziko, ne automaticky důkaz problému.
- verdict a summary piš stručně česky. positives/warnings maximálně 3 krátké položky.

Inzerát:
Model: {item.model or 'neuveden'}
Titulek: {item.title}
Cena: {item.price if item.price is not None else 'neuvedena'} Kč
Rok: {item.year if item.year is not None else 'neuveden'}
Nájezd: {item.mileage_km if item.mileage_km is not None else 'neuveden'} km
Palivo: {item.fuel or 'neuvedeno'}
Převodovka: {item.transmission or 'neuvedena'}
Detekovaná výbava: {item.equipment_text or 'neuvedena'}

Aktuální peer kontext:
{peer_context}

Text detailu inzerátu:
{detail or 'Detail se nepodařilo načíst; hodnoť jen dostupná data.'}
""".strip()

        try:
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Jsi konzervativní poradce pro nákup ojetých aut. "
                            "Vrať pouze strukturované JSON hodnocení podle zadaného schématu."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "car_evaluation",
                        "strict": True,
                        "schema": EVALUATION_SCHEMA,
                    },
                },
            }
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            r.raise_for_status()
            response = r.json()
            content = response["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError("empty AI response")
            data = json.loads(content)
        except Exception as exc:
            print(f"[WARN] AI evaluation failed for {item.title}: {exc}")
            return False

        item.score = int(data["score"])
        item.ai_value_score = int(data["value_score"])
        item.ai_equipment_score = int(data["equipment_score"])
        item.ai_reliability_score = int(data["reliability_score"])
        item.ai_verdict = str(data["verdict"]).strip()
        item.ai_summary = str(data["summary"]).strip()
        item.ai_positives = [str(x).strip() for x in data.get("positives", []) if str(x).strip()]
        item.ai_warnings = [str(x).strip() for x in data.get("warnings", []) if str(x).strip()]
        item.match_reason = "AI hodnocení"
        return True
