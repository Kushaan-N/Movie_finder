"""Resolve a showtime to its chain seat-selection URL.

SerpApi's ``link`` for a showing is a ``google.com/search`` URL, not a seat page,
so seat verification had nothing to open. This module bridges that gap: given a
showtime we already know about (theater, movie, start time), it finds the chain's
own seat URL by reading the chain's public showtimes listing.

Verified live on 2026-07-29:

* **Cinemark** — the theatre page for a date lists showtimes as plain anchors to
  ``/TicketSeatMap/?TheaterId=..&ShowtimeId=..``. One page load per theatre/date
  yields every showtime's seat URL, so results are cached per (theatre, date).
* **AMC** — the theatre showtimes page links each showing to ``/showtimes/<id>``,
  and the seat map is that URL plus ``/seats``. Same one-load-per-date shape.
* **Regal** — deliberately unsupported. Its showtime URLs are discoverable, but
  the seat page itself is behind a Cloudflare CAPTCHA, so there is nothing to
  resolve *to*. See scrape_selectors.json.

Matching a showtime to a link is done on start time within a tolerance, because
Google's reported time and the chain's own listing occasionally differ by a
minute or two. When several candidates tie, the closest in time wins.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

logger = logging.getLogger("showtime_finder.resolver")

# How far apart the provider's time and the chain's listed time may be.
_TIME_TOLERANCE_MIN = 3

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.IGNORECASE)


def _parse_time_label(text: str) -> Optional[tuple[int, int]]:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "p":
        hour += 12
    return hour, int(m.group(2))


def _minutes_apart(hm: tuple[int, int], target: datetime) -> int:
    return abs((hm[0] * 60 + hm[1]) - (target.hour * 60 + target.minute))


def _best_match(candidates: list[tuple[str, str]], target: datetime) -> Optional[str]:
    """Pick the URL whose time label is closest to ``target`` within tolerance.

    ``candidates`` is a list of (time_label_text, url).
    """
    best: Optional[str] = None
    best_delta = _TIME_TOLERANCE_MIN + 1
    for label, url in candidates:
        hm = _parse_time_label(label)
        if not hm:
            continue
        delta = _minutes_apart(hm, target)
        if delta < best_delta:
            best_delta, best = delta, url
    return best


# --- per-chain listing parsers (pure functions over HTML, unit-testable) ---- #

def cinemark_candidates(html: str, base: str = "https://www.cinemark.com") -> list[tuple[str, str]]:
    """Extract (time_label, seat_url) pairs from a Cinemark theatre page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.select('a[href*="TicketSeatMap"]'):
        label = a.get_text(" ", strip=True)
        href = a.get("href")
        if label and href:
            out.append((label, urljoin(base, href)))
    return out


def amc_candidates(html: str, base: str = "https://www.amctheatres.com") -> list[tuple[str, str]]:
    """Extract (time_label, seat_url) pairs from an AMC showtimes page.

    AMC links a showing to ``/showtimes/<numeric id>``; the seat map is that URL
    with ``/seats`` appended.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, str]] = []
    for a in soup.select('a[href*="/showtimes/"]'):
        href = a.get("href") or ""
        if not re.search(r"/showtimes/\d+", href):
            continue
        label = a.get_text(" ", strip=True)
        if not label:
            continue
        seat_url = urljoin(base, href.rstrip("/") + "/seats")
        out.append((label, seat_url))
    return out


# --- listing URLs ----------------------------------------------------------- #

def cinemark_listing_url(theater_slug: str, day: datetime) -> str:
    return (
        f"https://www.cinemark.com/theatres/{theater_slug.strip('/')}"
        f"?showDate={day:%Y-%m-%d}"
    )


def amc_listing_url(theater_slug: str, day: datetime) -> str:
    return (
        f"https://www.amctheatres.com/movie-theatres/{theater_slug.strip('/')}"
        f"/showtimes?date={day:%Y-%m-%d}"
    )


_PARSERS = {"cinemark": cinemark_candidates, "amc": amc_candidates}
_LISTINGS = {"cinemark": cinemark_listing_url, "amc": amc_listing_url}


def supports(chain: str) -> bool:
    return (chain or "").lower() in _PARSERS


def listing_url(chain: str, theater_slug: str, day: datetime) -> Optional[str]:
    builder = _LISTINGS.get((chain or "").lower())
    return builder(theater_slug, day) if builder and theater_slug else None


def resolve_from_listing(
    chain: str, html: str, start: datetime, base: Optional[str] = None
) -> Optional[str]:
    """Find the seat URL for ``start`` in a chain listing page's HTML.

    ``base`` is the URL the listing was fetched from; relative hrefs resolve
    against it, so this works unchanged when the host is swapped (E2E tests point
    it at a local fixture server).
    """
    parser = _PARSERS.get((chain or "").lower())
    if not parser:
        return None
    candidates = parser(html, base) if base else parser(html)
    return _best_match(candidates, start)
