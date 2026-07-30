"""Search orchestration: providers -> filters -> seat check -> response."""
from __future__ import annotations

import hashlib
import logging
import re
import time as _time
from datetime import date, datetime, time, timedelta

from ..config import get_settings
from ..formats import format_matches_any
from ..providers.base import ProviderQuery, ProviderShowtime
from ..providers.demo_provider import DemoProvider
from ..providers.movieglu_provider import MovieGluProvider
from ..providers.scraper_provider import ScraperProvider
from ..providers.serpapi_provider import SerpApiProvider
from ..schemas import SearchRequest, SearchResponse, SearchMeta, Showtime
from .seatcheck import check_seats
from .theaters import Theater, candidate_theaters, load_theaters

logger = logging.getLogger("showtime_finder.search")

DEFAULT_RANGE_DAYS = 14

# Provider priority order (highest first). DemoProvider only activates when no
# real provider is configured (see its .available()).
_PROVIDERS = [SerpApiProvider, MovieGluProvider, ScraperProvider, DemoProvider]

# Tiny in-process TTL cache for search responses, keyed on the request. Conserves
# the SerpApi free-tier quota and speeds repeat searches. Fine for single-process
# v1; swap for Redis when scaling to multiple workers.
_search_cache: dict[str, tuple[float, SearchResponse]] = {}


def _requested_formats(req: SearchRequest) -> list[str]:
    selected = req.formats or [req.format]
    cleaned = [f.strip() for f in selected if f and f.strip()]
    if not cleaned or any(f.lower() == "any" for f in cleaned):
        return []
    return list(dict.fromkeys(cleaned))


def _format_matches_any(actual: str, requested: list[str]) -> bool:
    # Hierarchical: requesting "IMAX" accepts "70mm IMAX". See app.formats.
    return format_matches_any(actual, requested)


def _cache_key(req: SearchRequest, start: date, end: date) -> str:
    formats = _requested_formats(req)
    raw = "|".join(
        str(x) for x in (
            req.movie_title.strip().lower(), ",".join(sorted(f.lower() for f in formats)),
            req.location.strip().lower(),
            req.radius_miles, start, end, req.time_rule.weekday_cutoff,
            req.time_rule.weekends_unrestricted, req.seats_together, req.min_row,
        )
    )
    return hashlib.sha1(raw.encode()).hexdigest()


def clear_search_cache() -> None:
    _search_cache.clear()


def _apply_date_defaults(req: SearchRequest) -> tuple[date, date]:
    today = datetime.now().date()
    start = req.date_from or today
    end = req.date_to or (today + timedelta(days=DEFAULT_RANGE_DAYS))
    if end < start:
        end = start
    return start, end


def _parse_cutoff(hhmm: str) -> time:
    try:
        h, m = hhmm.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return time(18, 30)


def _passes_time_rule(dt: datetime, cutoff: time, weekends_unrestricted: bool) -> bool:
    is_weekend = dt.weekday() >= 5  # Sat=5, Sun=6
    if is_weekend and weekends_unrestricted:
        return True
    return dt.time() >= cutoff


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _match_theater(provider_name: str, theaters: list[Theater]) -> Theater | None:
    """Best-effort match a provider theater name to a theaters.json entry so we
    can attach chain, distance and booking base."""
    pn = _normalize_name(provider_name)
    if not pn:
        return None
    best: Theater | None = None
    best_score = 0
    for t in theaters:
        tn = _normalize_name(t.name)
        # token overlap score
        pt, tt = set(pn.split()), set(tn.split())
        if not pt or not tt:
            continue
        overlap = len(pt & tt)
        if overlap > best_score:
            best_score = overlap
            best = t
    # Require at least two shared tokens (e.g. "amc" + "metreon") to trust it.
    return best if best_score >= 2 else None


def _showtime_key(theater_name: str, movie: str, dt: datetime, fmt: str) -> str:
    raw = f"{_normalize_name(theater_name)}|{_normalize_name(movie)}|{dt.isoformat()}|{fmt.lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


async def run_search(req: SearchRequest, use_cache: bool = True) -> SearchResponse:
    start, end = _apply_date_defaults(req)
    requested_formats = _requested_formats(req)

    ttl = get_settings().search_cache_ttl_sec
    cache_key = _cache_key(req, start, end)
    if use_cache and ttl > 0:
        hit = _search_cache.get(cache_key)
        if hit and (_time.time() - hit[0]) < ttl:
            return hit[1].model_copy(deep=True)

    cutoff = _parse_cutoff(req.time_rule.weekday_cutoff)
    theaters = load_theaters()
    notes: list[str] = []

    # Distance prefilter from our theater list (used for radius filtering and to
    # know which theaters to ask a provider about).
    #
    # Deliberately NOT format-filtered. theaters.json format lists are
    # hand-maintained and go stale — AMC Eastridge 15 was listed Dolby/Standard
    # while Google was reporting real IMAX showings there — so using them to pick
    # which theaters to even query silently hid showtimes that exist. Formats are
    # filtered below, against what the provider actually reports. Cost: a
    # format-narrowed search now queries the same number of theaters as an "Any"
    # search, so the radius is what bounds provider quota.
    candidates = candidate_theaters(req.location, req.radius_miles, [])
    dist_by_theater_id = {t.id: d for t, d in candidates}

    query = ProviderQuery(
        movie_title=req.movie_title,
        # Fetch all formats once, then apply the multi-select OR filter below.
        fmt="Any",
        location=req.location,
        date_from=start,
        date_to=end,
        theaters=[t for t, _ in candidates],
    )

    provider_used = "none"
    raw: list[ProviderShowtime] = []
    for provider_cls in _PROVIDERS:
        provider = provider_cls()
        if not provider.available():
            continue
        try:
            raw = await provider.fetch(query)
        except Exception as exc:  # pragma: no cover - provider/network errors
            logger.warning("Provider %s failed: %s", provider.name, exc)
            notes.append(f"Provider {provider.name} errored; falling back.")
            continue
        provider_used = provider.name
        if raw:
            break
        notes.append(f"Provider {provider.name} returned no rows; falling back.")

    if provider_used == "serpapi" and not raw:
        if not candidates:
            notes.append(
                "No theaters matched this location, radius and format combination, so "
                "there was nothing to look up. Widen the radius, loosen the format "
                "filter, or add theaters to theaters.json."
            )
        else:
            looked_up = ", ".join(t.name for t, _ in candidates)
            notes.append(
                f"SerpApi found no '{req.movie_title}' showtimes at the "
                f"{len(candidates)} theater(s) in range ({looked_up}) for the selected "
                "dates. Most often the title doesn't match Google's exactly or the "
                "movie isn't playing there — check the spelling and the date range. "
                "If this persists, confirm your SERPAPI_KEY still has quota at "
                "serpapi.com/account."
            )
    if provider_used == "demo":
        notes.append(
            "Showing DEMO data (synthetic showtimes) because no provider is "
            "configured. Set SERPAPI_KEY in backend/.env for live results."
        )
    if provider_used == "none":
        notes.append(
            "No data provider configured. Set SERPAPI_KEY in backend/.env to get "
            "live showtimes (100 free searches/month at serpapi.com)."
        )

    showtimes: list[Showtime] = []
    # Track why rows were dropped so an empty result can explain itself instead of
    # just saying "no showtimes matched".
    dropped_format: set[str] = set()
    dropped_time = 0
    dropped_radius = 0
    for st in raw:
        if not _format_matches_any(st.format, requested_formats):
            dropped_format.add(st.format)
            continue
        # Time-of-day / day-of-week rule.
        if not _passes_time_rule(st.start_datetime, cutoff, req.time_rule.weekends_unrestricted):
            dropped_time += 1
            continue

        matched = _match_theater(st.theater_name, theaters)
        chain = st.chain or (matched.chain if matched else "unknown")
        theater_id = matched.id if matched else _normalize_name(st.theater_name).replace(" ", "-")
        # Prefer the provider's own distance (SerpApi reports it relative to the
        # searched location); fall back to our geocoded theaters.json distance.
        distance = st.distance_miles
        if distance is None and matched:
            distance = dist_by_theater_id.get(matched.id)

        # Radius filter whenever we have a distance at all (now works on live data).
        if distance is not None and distance > req.radius_miles:
            dropped_radius += 1
            continue

        seat_check = check_seats(
            st, chain=chain, theater_id=theater_id,
            seats_together=req.seats_together, min_row=req.min_row,
        )

        showtimes.append(
            Showtime(
                key=_showtime_key(st.theater_name, st.movie_title, st.start_datetime, st.format),
                theater_id=theater_id,
                theater_name=st.theater_name,
                chain=chain,
                address=st.theater_address or (matched.address if matched else None),
                distance_miles=distance,
                movie_title=st.movie_title,
                format=st.format,
                start_datetime=st.start_datetime,
                start_time_label=st.start_datetime.strftime("%-I:%M %p"),
                booking_url=st.booking_url,
                seat_check=seat_check,
            )
        )

    # The provider found the movie but every row was filtered out locally. Say
    # which filter did it — otherwise this is indistinguishable from "not playing".
    if raw and not showtimes:
        if dropped_format:
            offered = ", ".join(sorted(dropped_format))
            notes.append(
                f"Found {len(raw)} showtime(s) for '{req.movie_title}', but none in "
                f"{' or '.join(requested_formats)}. Available format(s) here: {offered}. "
                "Adjust the format filter to see them."
            )
        if dropped_time:
            notes.append(
                f"{dropped_time} showtime(s) were dropped by the "
                f"{req.time_rule.weekday_cutoff} weekday cutoff. Lower the cutoff to "
                "include earlier screenings."
            )
        if dropped_radius:
            notes.append(
                f"{dropped_radius} showtime(s) were outside the "
                f"{req.radius_miles:g}-mile radius."
            )

    # Stable ordering: theater name, then datetime.
    showtimes.sort(key=lambda s: (s.theater_name.lower(), s.start_datetime))

    # Optional seat verification (Playwright): upgrade "check manually" badges for
    # real-provider results only — never scrape synthetic demo booking URLs.
    if provider_used in ("serpapi", "movieglu"):
        from ..scrape.verifier import SeatVerifier

        verifier = SeatVerifier()
        if verifier.available():
            verified, vnotes = await verifier.enrich(showtimes, req.seats_together, req.min_row)
            notes.extend(vnotes)
            if verified:
                notes.append(f"Seat-verified {verified} showtime(s) via Playwright seat maps.")

    meta = SearchMeta(
        provider_used=provider_used,
        theaters_considered=len(candidates) or len(theaters),
        showtimes_returned=len(showtimes),
        notes=notes,
    )
    response = SearchResponse(meta=meta, showtimes=showtimes)

    # Only cache real, non-empty results — never cache "none"/empty so a fixed
    # config or newly-added key takes effect on the next search immediately.
    if use_cache and ttl > 0 and provider_used != "none" and showtimes:
        _search_cache[cache_key] = (_time.time(), response.model_copy(deep=True))

    return response
