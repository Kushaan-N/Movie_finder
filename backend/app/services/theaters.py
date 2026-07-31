"""Theater resolution + geo helpers.

v1 resolves candidate theaters from the editable theaters.json. If a Google
Places key is configured this is where a live lookup would slot in (same return
shape), so the rest of the pipeline doesn't change.
"""
from __future__ import annotations

import json
import logging
import math
import re
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
    # Path to this theater on its own chain website, used to resolve a showtime to
    # its seat-selection page (SerpApi only hands back google.com links).
    chain_slug: str = ""


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
                chain_slug=t.get("chain_slug", ""),
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


_ZIP_RE = re.compile(r"\b\d{5}\b")


def _normalize_location(text: str) -> str:
    """Lowercase and reduce to space-separated words, so punctuation can't matter."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@lru_cache
def _geocode_table() -> dict[str, tuple[float, float]]:
    """The hand-written table, widened with every city and ZIP in theaters.json.

    Each theater already carries an address and coordinates, so the places we
    actually serve can geocode themselves — one less list to keep in sync by hand.
    Hand-written entries win, since they name the centre of a city rather than
    wherever a cinema happens to sit in it.
    """
    table: dict[str, tuple[float, float]] = {}
    for t in load_theaters():
        if t.lat is None or t.lng is None or not t.address:
            continue
        coords = (t.lat, t.lng)
        # "135 4th St #3000, San Francisco, CA 94103" -> city "san francisco", zip 94103
        parts = [p.strip() for p in t.address.split(",") if p.strip()]
        if len(parts) >= 2:
            table.setdefault(_normalize_location(parts[-2]), coords)
        for zipcode in _ZIP_RE.findall(t.address):
            table.setdefault(zipcode, coords)
    table.update(_FALLBACK_GEOCODE)
    return table


def geocode(location: str) -> Optional[tuple[float, float]]:
    """Best-effort geocode. Returns (lat, lng) or None.

    Offline, and deliberately forgiving about how the place is written: people
    type "San Francisco, CA", not "san francisco". Matching used to be exact-key
    plus single-token, which meant every multi-word city was unreachable the
    moment a state was appended — and an unplaceable location silently disables
    the radius, so the failure is worth some effort to avoid.

    Wire Google Places in here for real coverage; the return shape is the contract.
    """
    norm = _normalize_location(location)
    if not norm:
        return None
    table = _geocode_table()
    if norm in table:
        return table[norm]
    # A ZIP anywhere in the string is the most specific thing on offer.
    for zipcode in _ZIP_RE.findall(norm):
        if zipcode in table:
            return table[zipcode]
    # Otherwise the longest place name that appears as whole words, so
    # "downtown san jose ca" resolves and "san" alone never does.
    for key in sorted((k for k in table if not k.isdigit()), key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", norm):
            return table[key]
    logger.info("No offline geocode for '%s'; distance filtering will be skipped.", location)
    return None


def candidate_theaters(
    location: str, radius_miles: float, formats: str | list[str]
) -> list[tuple[Theater, Optional[float]]]:
    """Return (theater, distance_miles) within radius, optionally format-filtered.

    If the location can't be geocoded, distance is None and we return all
    theaters (distance filtering is simply skipped rather than failing).
    """
    origin = geocode(location)
    requested = [formats] if isinstance(formats, str) else formats
    requested = [f for f in requested if f and f.lower() != "any"]
    theaters = load_theaters()
    out: list[tuple[Theater, Optional[float]]] = []
    for t in theaters:
        if requested and not any(f.lower() in {tf.lower() for tf in t.formats} for f in requested):
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
