"""SerpApi Google Showtimes provider (priority 1).

Uses SerpApi's regular ``google`` engine, which returns a ``showtimes`` block
for *theater-name* queries (e.g. "AMC Metreon 16"). Each response covers several
upcoming days (each entry carries a relative ``day``/``date`` label that we
convert to a real calendar date and then filter to the requested range).

Google serves two different shapes under ``showtimes``, and we handle both:

* **theater-keyed** (what theater-name queries return): each day block holds a
  ``movies`` list, and each entry's ``name`` is the *movie*. We keep only the
  movie the user asked for.
* **movie-keyed** (what a "<movie> showtimes" query used to return): each day
  block holds a ``theaters`` list. Google no longer reliably serves this widget
  to scrapers, so we don't rely on it — but we still parse it if it shows up.

Because the working shape is keyed on theaters, one search fans out to one
SerpApi request *per candidate theater*. That costs quota (free tier is 250
searches/month), which is why ``search_cache_ttl_sec`` and the theaters.json
radius prefilter matter: they bound how many theaters we ever ask about.

SerpApi does not expose seat maps, so every showtime here comes back with seat
status "check manually" downstream (which is correct, not a limitation to paper
over).

Docs: https://serpapi.com/showtimes-results
"""
from __future__ import annotations

import asyncio
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

# Max concurrent SerpApi requests during the per-theater fan-out. SerpApi's free
# tier allows 250 searches/hour, so a small cap is plenty and stays polite.
_MAX_CONCURRENCY = 5

# Words that carry no signal when comparing a requested title to Google's title.
_TITLE_STOPWORDS = {"the", "a", "an"}


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


def _title_tokens(title: str) -> set[str]:
    words = re.split(r"[^a-z0-9]+", (title or "").lower())
    tokens = {w for w in words if w and w not in _TITLE_STOPWORDS}
    return tokens or {w for w in words if w}


def _titles_match(requested: str, actual: str) -> bool:
    """Whether Google's movie title refers to the movie the user asked for.

    Google decorates titles ("The Odyssey", "Dune: Part Two", "Wicked: For
    Good"), so we compare on significant tokens and accept a subset match in
    either direction rather than requiring string equality.
    """
    req, act = _title_tokens(requested), _title_tokens(actual)
    if not req or not act:
        return False
    return req <= act or act <= req


class SerpApiProvider(ShowtimeProvider):
    name = "serpapi"

    def available(self) -> bool:
        return get_settings().has_serpapi

    async def fetch(self, query: ProviderQuery) -> list[ProviderShowtime]:
        """Fan out one showtimes query per candidate theater.

        Google only serves the showtimes widget for theater-name queries, so the
        theater list drives the requests. With no candidate theaters we fall back
        to a single movie-title query (one search, parsed movie-keyed).
        """
        if not query.theaters:
            data = await self._request(f"{query.movie_title} showtimes", query.location)
            return self._parse(data, query) if data else []

        sem = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def one(theater) -> list[ProviderShowtime]:
            async with sem:
                data = await self._request(theater.name, query.location)
            if not data:
                return []
            return self._parse_theater_keyed(data, query, theater)

        results = await asyncio.gather(
            *(one(t) for t in query.theaters), return_exceptions=True
        )
        out: list[ProviderShowtime] = []
        for theater, res in zip(query.theaters, results):
            if isinstance(res, BaseException):
                logger.warning("SerpApi lookup for %s failed: %s", theater.name, res)
                continue
            out.extend(res)
        return out

    async def _request(self, q: str, location: str) -> dict | None:
        """One SerpApi call. Returns None on transport or API-level failure."""
        params = {
            "engine": "google",
            "q": q,
            "hl": "en",
            "gl": "us",
            "api_key": get_settings().serpapi_key,
        }
        if location:
            params["location"] = location

        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.get(_ENDPOINT, params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("SerpApi request failed for %r: %s", q, exc)
            return None

        if data.get("error"):
            # Quota exhaustion and bad keys both land here — surface it loudly.
            logger.warning("SerpApi returned an error for %r: %s", q, data["error"])
            return None
        return data

    def _iter_days(self, data: dict, query: ProviderQuery):
        """Yield (day, block) for day blocks inside the requested date range."""
        today = datetime.now().date()
        for block in data.get("showtimes", []):
            day = _resolve_date(block.get("day", ""), block.get("date", ""), today)
            if not day:
                continue
            if day < query.date_from or day > query.date_to:
                continue
            yield day, block

    def _rows(
        self,
        showing_list: list,
        day: date,
        query: ProviderQuery,
        *,
        theater_name: str,
        movie_title: str,
        address: str | None,
        link: str | None,
        chain: str | None = None,
        distance: float | None = None,
    ) -> list[ProviderShowtime]:
        out: list[ProviderShowtime] = []
        for showing in showing_list or []:
            fmt = self._normalize_format(showing.get("type", "Standard"))
            if not self._format_matches(query.fmt, fmt):
                continue
            for t in showing.get("time", []):
                start = _combine(day, t)
                if not start:
                    continue
                out.append(
                    ProviderShowtime(
                        theater_name=theater_name,
                        theater_address=address,
                        movie_title=movie_title,
                        format=fmt,
                        start_datetime=start,
                        chain=chain,
                        booking_url=link,
                        distance_miles=distance,
                        seat_rows=None,  # SerpApi has no seat map
                        seat_unavailable_reason="SerpApi does not expose seat maps",
                    )
                )
        return out

    def _parse_theater_keyed(
        self, data: dict, query: ProviderQuery, theater
    ) -> list[ProviderShowtime]:
        """Parse a theater-name response: day blocks -> ``movies`` -> showings.

        We asked about one theater, so the theater identity comes from our own
        theaters.json entry (exact, and already geocoded) rather than the SERP.
        """
        out: list[ProviderShowtime] = []
        for day, block in self._iter_days(data, query):
            for movie in block.get("movies", []):
                name = movie.get("name", "")
                if not _titles_match(query.movie_title, name):
                    continue
                out.extend(
                    self._rows(
                        movie.get("showing", []), day, query,
                        theater_name=theater.name,
                        # Keep Google's title: it disambiguates re-releases.
                        movie_title=name or query.movie_title,
                        address=theater.address,
                        link=movie.get("link") or theater.booking_base_url,
                        chain=theater.chain,
                    )
                )
        return out

    def _parse(self, data: dict, query: ProviderQuery) -> list[ProviderShowtime]:
        """Parse a movie-keyed response: day blocks -> ``theaters`` -> showings."""
        out: list[ProviderShowtime] = []
        for day, block in self._iter_days(data, query):
            for theater in block.get("theaters", []):
                out.extend(
                    self._rows(
                        theater.get("showing", []), day, query,
                        theater_name=theater.get("name", "Unknown Theater"),
                        movie_title=query.movie_title,
                        address=theater.get("address"),
                        link=theater.get("link"),
                        distance=_parse_distance(theater.get("distance", "")),
                    )
                )
        return out

    @staticmethod
    def _normalize_format(raw: str) -> str:
        raw = (raw or "").strip()
        low = raw.lower()
        if "imax" in low and "70mm" in low:
            return "70mm IMAX"
        if "imax" in low:
            return "IMAX"
        if "70mm" in low:
            return "70mm"
        if "dolby" in low:
            return "Dolby"
        if "4dx" in low:
            return "4DX"
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
