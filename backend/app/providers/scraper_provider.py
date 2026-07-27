"""Playwright scraping fallback (priority 3).

Polite, rate-limited, robots-aware plumbing (RateLimiter + _robots_allows) shared
by the seat verifier. Import-safe even when Playwright isn't installed (the import
is lazy) and OFF unless ENABLE_SCRAPER_FALLBACK=true.

Seat-map parsing/verification is genuinely hard (canvas rendering, login walls,
bot walls). It lives in app/scrape/ and always returns "check manually" rather
than guessing when a map can't be read.
"""
from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser
from urllib.parse import urlparse

from ..config import get_settings
from .base import ProviderQuery, ProviderShowtime, ShowtimeProvider

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


# NOTE: seat-map PARSING now lives in app/scrape/seatmap.py (config-driven, fully
# unit-tested), and seat VERIFICATION (render a booking page + parse it) lives in
# app/scrape/verifier.py. Those reuse RateLimiter and _robots_allows above. This
# provider remains a showtime *discovery* fallback only.


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
        # Showtime *discovery* by scraping chain sites is the fragile part and is
        # left as a documented stub: it returns nothing so the pipeline falls back
        # to higher-priority providers cleanly. The high-value seat *verification*
        # (render a known booking page and parse its seat map) is implemented in
        # app/scrape/verifier.py and runs as an enrichment step in run_search.
        logger.info("Scraper discovery is a stub; seat verification lives in scrape/verifier.py.")
        return []
