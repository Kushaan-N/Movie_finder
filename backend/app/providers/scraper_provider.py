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
import urllib.request
from urllib.parse import urlparse

from .. import robots
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


_robots_cache: dict[str, "robots.RobotsFile"] = {}
# Roots whose robots.txt could not be READ (bot check, queue page, empty body),
# mapped to when we gave up.
#
# Two reasons this is separate from the deny-all cache entry. It distinguishes
# "the site forbids this" from "we could not read the file" — both end in "don't
# fetch", but only the first is the site's decision, and reporting the wrong one
# accuses the site of a policy it does not have. And it *expires*: AMC serves a
# Queue-it waiting room in place of robots.txt under load, so being unreadable is
# a bad minute, not a standing condition. Without the expiry one queued fetch
# disables that chain for the life of the process.
_robots_unreadable: dict[str, float] = {}
_ROBOTS_RETRY_AFTER_SEC = 300.0
_DENY_ALL = robots.parse("User-agent: *\nDisallow: /")

# Theater chains reject urllib's default User-Agent outright (both amctheatres.com
# and cinemark.com answer robots.txt with 403 to "Python-urllib/x.y"), which made
# every robots check fail closed and silently disabled seat verification entirely.
# We fetch robots.txt with the same browser-like UA we use for pages so we can
# actually READ the rules — and then obey them.
_ROBOTS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 showtime-finder/1.0"
)


def robots_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def cache_robots(root: str, body: str) -> bool:
    """Parse and cache a robots.txt body for ``root``.

    Returns False (and caches deny-all) when the body cannot be trusted as this
    host's real robots.txt. Two cases, both seen live on 2026-07-29:

    * the body is HTML — amctheatres.com serves a bot-check page to non-browser
      clients;
    * the body has no directives at all — cinemark.com answers with a Cloudflare
      "Performing security verification" interstitial, whose *text* parses to zero
      rules and would therefore read as "allow everything".

    A genuinely empty robots.txt does mean allow-all, but we cannot tell one from
    a challenge page, so we fail closed rather than scrape on a guess.
    """
    try:
        rf = robots.parse(body)
    except robots.NotRobotsTxt as exc:
        logger.info("robots.txt for %s is not parseable (%s); treating as deny.", root, exc)
        _robots_cache[root] = _DENY_ALL
        _robots_unreadable[root] = time.time()
        return False
    if rf.empty:
        logger.info("robots.txt for %s had no directives; treating as deny.", root)
        _robots_cache[root] = _DENY_ALL
        _robots_unreadable[root] = time.time()
        return False
    _robots_cache[root] = rf
    _robots_unreadable.pop(root, None)
    return True


def robots_unreadable(root: str) -> bool:
    """True when ``root`` is deny-all because we could not READ its robots.txt.

    Expires after ``_ROBOTS_RETRY_AFTER_SEC``: on expiry the deny-all is dropped
    too, so the next check re-fetches instead of inheriting a stale verdict.
    """
    stamp = _robots_unreadable.get(root)
    if stamp is None:
        return False
    if time.time() - stamp >= _ROBOTS_RETRY_AFTER_SEC:
        _robots_unreadable.pop(root, None)
        _robots_cache.pop(root, None)
        return False
    return True


def _fetch_robots(root: str) -> robots.RobotsFile:
    """Read and parse ``root/robots.txt`` over plain HTTP. Raises on failure."""
    req = urllib.request.Request(f"{root}/robots.txt", headers={"User-Agent": _ROBOTS_UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "replace")
    return robots.parse(body)


def _robots_allows(url: str, user_agent: str = "showtime-finder") -> bool:
    root = robots_root(url)
    rf = _robots_cache.get(root)
    if rf is None:
        try:
            rf = _fetch_robots(root)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.info("robots.txt unreadable for %s (%s); skipping this host.", root, exc)
            # Be conservative: if we can't read robots, don't scrape.
            _robots_cache[root] = _DENY_ALL
            return False
        _robots_cache[root] = rf
    return rf.can_fetch(user_agent, url)


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
