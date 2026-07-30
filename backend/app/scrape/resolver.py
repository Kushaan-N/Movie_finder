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
from urllib.parse import urljoin, urlparse

from ..titles import slug_matches_title

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


def regal_sold_out(html: str, movie_title: str) -> list[tuple[str, bool]]:
    """Extract (time_label, sold_out) for one movie from a Regal theatre page.

    Regal's seat page is CAPTCHA-gated, but its *listing* is not, and it marks
    sold-out showings semantically: ``<button disabled aria-label="10:30pm
    showtime, sold out">``. A sold-out showing answers the seat question
    definitively — no seats at all means no N-together — so this yields a real
    result for the one case it can prove.

    Showtimes must be attributed to the right film: a busy listing carries 100+
    times across many movies. Each showtime's nearest ancestor containing a
    ``/movies/<slug>`` link identifies the film (the CSS classes are generated
    hashes and unusable, but that link is stable).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, bool]] = []
    for btn in soup.find_all("button"):
        label = btn.get_text(" ", strip=True)
        if not _TIME_RE.search(label):
            continue
        slug = None
        for parent in btn.parents:
            link = parent.find("a", href=True) if hasattr(parent, "find") else None
            if link and "/movies/" in link["href"]:
                slug = link["href"]
                break
        if not slug or not slug_matches_title(movie_title, slug.rsplit("/", 1)[-1]):
            continue
        aria = (btn.get("aria-label") or "").lower()
        sold = btn.has_attr("disabled") or "sold out" in aria or "sold out" in label.lower()
        out.append((label, sold))
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


def regal_listing_url(theater_slug: str, day: datetime) -> str:
    # Regal's theatre page takes a MM-DD-YYYY date and renders that day's
    # showtimes, including sold-out state. The seat page beyond it is CAPTCHA-gated.
    return f"https://www.regmovies.com/theatres/{theater_slug.strip('/')}?date={day:%m-%d-%Y}"


_PARSERS = {"cinemark": cinemark_candidates, "amc": amc_candidates}
_LISTINGS = {
    "cinemark": cinemark_listing_url,
    "amc": amc_listing_url,
    "regal": regal_listing_url,
}


def supports(chain: str) -> bool:
    """Whether a showtime can be resolved to a seat page for this chain."""
    return (chain or "").lower() in _PARSERS


def find_sold_out(chain: str, html: str, movie_title: str, start: datetime) -> Optional[bool]:
    """Sold-out state for a showtime from a chain listing, if it publishes one.

    Returns True/False when the listing says, or None when it doesn't expose
    capacity (Cinemark's listing carries no such signal at all).
    """
    if (chain or "").lower() != "regal":
        return None
    for label, sold in regal_sold_out(html, movie_title):
        hm = _parse_time_label(label)
        if hm and _minutes_apart(hm, start) <= _TIME_TOLERANCE_MIN:
            return sold
    return None


# Host -> chain. Used to attribute a grid the user read in their own browser, so
# the result isn't filed under "unknown" and per-theater row overrides can apply.
_HOSTS = {
    "amctheatres.com": "amc",
    "regmovies.com": "regal",
    "cinemark.com": "cinemark",
}


def chain_from_url(url: Optional[str]) -> Optional[str]:
    """Infer the chain from a seat-page URL."""
    if not url:
        return None
    host = (urlparse(url).netloc or "").lower()
    for domain, chain in _HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return chain
    return None


def theater_from_url(url: Optional[str], theaters) -> Optional[str]:
    """Match a seat-page URL to a theaters.json id via its chain_slug.

    A seat URL doesn't always carry the theatre slug (AMC's is just
    ``/showtimes/<id>/seats``), so this returns None rather than guessing when the
    path has nothing to match on.
    """
    if not url:
        return None
    chain = chain_from_url(url)
    path = (urlparse(url).path or "").lower()
    query = (urlparse(url).query or "").lower()
    best = None
    for t in theaters:
        if chain and t.chain != chain:
            continue
        slug = (t.chain_slug or "").lower()
        if not slug:
            continue
        # Match the most specific segment of the slug that appears in the URL.
        tail = slug.rsplit("/", 1)[-1]
        if tail and (tail in path or tail in query):
            if best is None or len(tail) > len(best[1]):
                best = (t.id, tail)
    return best[0] if best else None


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
