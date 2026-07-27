"""SerpApi Google Showtimes provider (priority 1).

Uses SerpApi's regular ``google`` engine, which returns a ``showtimes`` block
when the query is a movie + "showtimes". A single response covers several
upcoming days (each entry carries a relative ``day``/``date`` label that we
convert to a real calendar date and then filter to the requested range).

SerpApi does not expose seat maps, so every showtime here comes back with seat
status "check manually" downstream (which is correct, not a limitation to paper
over).

Docs: https://serpapi.com/showtimes-results
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

import httpx

from ..config import get_settings
from .base import ProviderQuery, ProviderShowtime, ShowtimeProvider

logger = logging.getLogger("showtime_finder.serpapi")

_ENDPOINT = "https://serpapi.com/search.json"

# Time entries like "7:15pm" / "10:00 am" / "7:15 PM".
_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)", re.IGNORECASE)
# Distance strings like "2.3 mi" / "0.9 miles" / "4 km".
_DIST_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(km|mi|mile)", re.IGNORECASE)
_KM_TO_MI = 0.621371


def _parse_distance(text: str) -> float | None:
    m = _DIST_RE.search(text or "")
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2).lower() == "km":
        value *= _KM_TO_MI
    return round(value, 1)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _combine(day: date, text: str) -> datetime | None:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    minute = int(m.group(2) or 0)
    if m.group(3).lower() == "pm":
        hour += 12
    return datetime(day.year, day.month, day.day, hour, minute)


def _resolve_date(day_label: str, date_label: str, today: date) -> date | None:
    """Turn Google's relative labels into a real date.

    Handles ``day`` values like "Today"/"Tomorrow"/"Fri" and ``date`` values
    like "Apr 5" / "5 Apr" / "4/5". Year is inferred (rolls to next year if the
    month/day has already passed this year).
    """
    dl = (day_label or "").strip().lower()
    if dl == "today":
        return today
    if dl == "tomorrow":
        return today + timedelta(days=1)

    ds = (date_label or "").strip().lower()
    if not ds:
        return None

    # "4/5" or "4-5"
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})$", ds)
    if m:
        month, dom = int(m.group(1)), int(m.group(2))
    else:
        # "apr 5" or "5 apr"
        tokens = re.split(r"[\s,]+", ds)
        month = dom = None
        for tok in tokens:
            if tok[:3] in _MONTHS:
                month = _MONTHS[tok[:3]]
            elif tok.isdigit():
                dom = int(tok)
        if month is None or dom is None:
            return None

    year = today.year
    try:
        candidate = date(year, month, dom)
    except ValueError:
        return None
    # If the date already passed by a wide margin, it's next year's occurrence.
    if candidate < today - timedelta(days=180):
        candidate = date(year + 1, month, dom)
    return candidate


class SerpApiProvider(ShowtimeProvider):
    name = "serpapi"

    def available(self) -> bool:
        return get_settings().has_serpapi

    async def fetch(self, query: ProviderQuery) -> list[ProviderShowtime]:
        settings = get_settings()
        params = {
            "engine": "google",
            "q": f"{query.movie_title} showtimes",
            "hl": "en",
            "gl": "us",
            "api_key": settings.serpapi_key,
        }
        if query.location:
            params["location"] = query.location

        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.get(_ENDPOINT, params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("SerpApi request failed: %s", exc)
            return []

        if data.get("error"):
            logger.warning("SerpApi returned an error: %s", data["error"])
            return []

        return self._parse(data, query)

    def _parse(self, data: dict, query: ProviderQuery) -> list[ProviderShowtime]:
        today = datetime.now().date()
        out: list[ProviderShowtime] = []
        for block in data.get("showtimes", []):
            day = _resolve_date(block.get("day", ""), block.get("date", ""), today)
            if not day:
                continue
            # Only keep days inside the requested range.
            if day < query.date_from or day > query.date_to:
                continue
            for theater in block.get("theaters", []):
                tname = theater.get("name", "Unknown Theater")
                taddr = theater.get("address")
                link = theater.get("link")
                dist = _parse_distance(theater.get("distance", ""))
                for showing in theater.get("showing", []):
                    fmt = self._normalize_format(showing.get("type", "Standard"))
                    if not self._format_matches(query.fmt, fmt):
                        continue
                    for t in showing.get("time", []):
                        start = _combine(day, t)
                        if not start:
                            continue
                        out.append(
                            ProviderShowtime(
                                theater_name=tname,
                                theater_address=taddr,
                                movie_title=query.movie_title,
                                format=fmt,
                                start_datetime=start,
                                booking_url=link,
                                distance_miles=dist,
                                seat_rows=None,  # SerpApi has no seat map
                                seat_unavailable_reason="SerpApi does not expose seat maps",
                            )
                        )
        return out

    @staticmethod
    def _normalize_format(raw: str) -> str:
        raw = (raw or "").strip()
        low = raw.lower()
        if "imax" in low:
            return "IMAX"
        if "dolby" in low:
            return "Dolby"
        if "screenx" in low:
            return "ScreenX"
        if low == "xd" or "xd" in low.split():
            return "XD"
        if not raw or "standard" in low or "digital" in low:
            return "Standard"
        return raw

    @staticmethod
    def _format_matches(requested: str, actual: str) -> bool:
        if not requested or requested.lower() in ("any", ""):
            return True
        return requested.lower() == actual.lower()
