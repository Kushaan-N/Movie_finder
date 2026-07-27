"""Theater resolution + geo helpers.

v1 resolves candidate theaters from the editable theaters.json. If a Google
Places key is configured this is where a live lookup would slot in (same return
shape), so the rest of the pipeline doesn't change.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from ..config import get_settings

logger = logging.getLogger("showtime_finder.theaters")


@dataclass
class Theater:
    id: str
    name: str
    chain: str
    address: str
    lat: Optional[float]
    lng: Optional[float]
    formats: list[str]
    booking_base_url: str


@lru_cache
def load_theaters() -> list[Theater]:
    settings = get_settings()
    try:
        with open(settings.theaters_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover
        logger.warning("Could not load theaters.json (%s).", exc)
        return []
    out: list[Theater] = []
    for t in data.get("theaters", []):
        out.append(
            Theater(
                id=t["id"],
                name=t["name"],
                chain=t.get("chain", "unknown"),
                address=t.get("address", ""),
                lat=t.get("lat"),
                lng=t.get("lng"),
                formats=t.get("formats", []),
                booking_base_url=t.get("booking_base_url", ""),
            )
        )
    return out


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3958.7613  # earth radius, miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# A tiny built-in geocode table so v1 works offline for common Bay Area inputs.
# With GOOGLE_PLACES_API_KEY set, real geocoding would replace this.
_FALLBACK_GEOCODE = {
    "94103": (37.7726, -122.4099),
    "94105": (37.7864, -122.3892),
    "san francisco": (37.7749, -122.4194),
    "sf": (37.7749, -122.4194),
    "95122": (37.3382, -121.8188),
    "san jose": (37.3382, -121.8863),
    "sj": (37.3382, -121.8863),
    "94568": (37.7161, -121.8994),
    "dublin": (37.7161, -121.8994),
    "94063": (37.4852, -122.2364),
    "redwood city": (37.4852, -122.2364),
}


def geocode(location: str) -> Optional[tuple[float, float]]:
    """Best-effort geocode. Returns (lat, lng) or None.

    v1 uses a small offline table; wire Google Places here for real coverage.
    """
    key = (location or "").strip().lower()
    if key in _FALLBACK_GEOCODE:
        return _FALLBACK_GEOCODE[key]
    # Match a bare ZIP embedded in a longer string.
    for token in key.replace(",", " ").split():
        if token in _FALLBACK_GEOCODE:
            return _FALLBACK_GEOCODE[token]
    logger.info("No offline geocode for '%s'; distance filtering will be skipped.", location)
    return None


def candidate_theaters(
    location: str, radius_miles: float, fmt: str
) -> list[tuple[Theater, Optional[float]]]:
    """Return (theater, distance_miles) within radius, optionally format-filtered.

    If the location can't be geocoded, distance is None and we return all
    theaters (distance filtering is simply skipped rather than failing).
    """
    origin = geocode(location)
    theaters = load_theaters()
    out: list[tuple[Theater, Optional[float]]] = []
    for t in theaters:
        if fmt and fmt.lower() not in ("any", "") and fmt not in t.formats:
            continue
        dist: Optional[float] = None
        if origin and t.lat is not None and t.lng is not None:
            dist = round(haversine_miles(origin[0], origin[1], t.lat, t.lng), 1)
            if dist > radius_miles:
                continue
        out.append((t, dist))
    # Nearest first when we have distances.
    out.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 0))
    return out
