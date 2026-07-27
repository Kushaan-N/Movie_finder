"""Playwright scraping fallback (priority 3).

This is the pass-2 structure: a polite, rate-limited, robots-aware scraper for
JS-rendered chain sites (Fandango / AMC / Regal / Cinemark), with a per-chain
seat-map parser registry that feeds the seat check. It is import-safe even when
Playwright is not installed (the import is lazy) and is OFF unless
ENABLE_SCRAPER_FALLBACK=true.

Seat-map parsing is genuinely hard (canvas rendering, login walls, bot walls).
When a parser can't reliably read the map it returns rows=None with a reason so
the result becomes "check manually" — we never guess a seat match.
"""
from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser
from typing import Callable, Optional
from urllib.parse import urlparse

from ..config import get_settings
from .base import ProviderQuery, ProviderShowtime, SeatMapRow, ShowtimeProvider

logger = logging.getLogger("showtime_finder.scraper")


class RateLimiter:
    """Simple async token-bucket-ish limiter: at most `rate` requests/second."""

    def __init__(self, rate_per_sec: float):
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _robots_allows(url: str, user_agent: str = "showtime-finder") -> bool:
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots_cache.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root}/robots.txt")
        try:
            rp.read()
        except Exception as exc:  # pragma: no cover - network dependent
            logger.info("robots.txt unreadable for %s (%s); skipping this host.", root, exc)
            # Be conservative: if we can't read robots, don't scrape.
            _robots_cache[root] = rp
            return False
        _robots_cache[root] = rp
    return rp.can_fetch(user_agent, url)


# --------------------------------------------------------------------------- #
# Per-chain seat-map parsers.
#
# Each takes a rendered Playwright `page` positioned on a seat-selection view
# and returns a list[SeatMapRow] in DOM order, or None (with the caller marking
# "check manually"). These are stubs to be filled in against live pages.
# --------------------------------------------------------------------------- #
SeatParser = Callable[..., "Optional[list[SeatMapRow]]"]


async def _parse_amc_seatmap(page) -> Optional[list[SeatMapRow]]:  # pragma: no cover
    # AMC renders seats as DOM elements grouped by row. Read rows top-to-bottom
    # (DOM order) and each seat's availability class. Return None if the map is
    # behind a login wall or rendered to canvas.
    return None


async def _parse_regal_seatmap(page) -> Optional[list[SeatMapRow]]:  # pragma: no cover
    return None


async def _parse_cinemark_seatmap(page) -> Optional[list[SeatMapRow]]:  # pragma: no cover
    return None


SEAT_PARSERS: dict[str, SeatParser] = {
    "amc": _parse_amc_seatmap,
    "regal": _parse_regal_seatmap,
    "cinemark": _parse_cinemark_seatmap,
}


class ScraperProvider(ShowtimeProvider):
    name = "scraper"

    def __init__(self) -> None:
        self._limiter = RateLimiter(get_settings().scrape_rate_limit_per_sec)

    def available(self) -> bool:
        if not get_settings().enable_scraper_fallback:
            return False
        try:
            import playwright  # noqa: F401
        except ImportError:
            logger.info("Scraper enabled but Playwright not installed; skipping.")
            return False
        return True

    async def fetch(self, query: ProviderQuery) -> list[ProviderShowtime]:
        # Pass-2 implementation outline (kept minimal and safe for v1):
        #
        #   async with async_playwright() as p:
        #       browser = await p.chromium.launch()
        #       for theater in query.theaters:
        #           if not _robots_allows(theater.booking_base_url): continue
        #           await self._limiter.acquire()
        #           page = await browser.new_page()
        #           ... navigate to showtimes, collect times + booking links ...
        #           parser = SEAT_PARSERS.get(theater.chain)
        #           rows = await parser(page) if parser else None
        #           ... build ProviderShowtime(seat_rows=rows) ...
        #
        # Until the per-chain navigation is filled in, return nothing so the
        # pipeline falls back to higher-priority providers cleanly.
        logger.info("Scraper fallback active but per-chain navigation is a v1 stub.")
        return []
