"""Playwright-based seat verification.

Given showtimes that already have booking URLs (from SerpApi etc.), this opens
each supported-chain booking page, renders it, and parses the seat map to
UPGRADE a "check manually" result into a real match / no-match with the physical
row shown. It does NOT rediscover showtimes — it enriches ones we already have,
which sidesteps the most fragile part of scraping and delivers the actual value
(seat verification honoring seats_together + min_row + row normalization).

Safety / politeness:
  * off unless ENABLE_SEAT_VERIFICATION=true and Playwright is installed
  * respects robots.txt, rate-limited, capped per search
  * per-URL TTL cache
  * import-safe (Playwright imported lazily)

Testability: the network/browser boundary is isolated in ``_get_html`` so the
enrichment logic can be unit-tested by monkeypatching it to return saved HTML.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from ..config import get_settings
from ..providers.scraper_provider import RateLimiter, _robots_allows
from ..schemas import Showtime
from ..services.seatcheck import evaluate_rows
from .seatmap import SeatMapParseResult, parse_seat_html

logger = logging.getLogger("showtime_finder.verifier")

# Chains we have seat-map selectors for.
SUPPORTED_CHAINS = {"amc", "regal", "cinemark"}

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 showtime-finder/1.0"
)


class SeatVerifier:
    def __init__(self) -> None:
        s = get_settings()
        self._limiter = RateLimiter(s.scrape_rate_limit_per_sec)
        self._cache_ttl = s.seat_verification_cache_ttl_sec
        self._cache: dict[str, tuple[float, SeatMapParseResult]] = {}
        self._pw = None
        self._browser = None
        self._context = None

    # --- availability ------------------------------------------------------ #
    def available(self) -> bool:
        if not get_settings().enable_seat_verification:
            return False
        try:
            import playwright  # noqa: F401
        except ImportError:
            logger.info("Seat verification enabled but Playwright not installed; skipping.")
            return False
        return True

    # --- browser lifecycle ------------------------------------------------- #
    async def _ensure_browser(self):  # pragma: no cover - needs a real browser
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=_USER_AGENT)

    async def _close(self):  # pragma: no cover - needs a real browser
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer:
                try:
                    await closer()
                except Exception:
                    pass
        self._pw = self._browser = self._context = None

    # --- the network/browser seam (monkeypatched in tests) ----------------- #
    async def _get_html(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Render ``url`` and return (html, reason). On failure html is None and
        reason explains why. Isolated so enrichment is testable without a browser."""
        # pragma: no cover below — exercised only with a live browser.
        await self._ensure_browser()  # pragma: no cover
        page = await self._context.new_page()  # pragma: no cover
        try:  # pragma: no cover
            await page.goto(url, wait_until="networkidle", timeout=20000)
            # Give the SPA a beat to render the seat map, then snapshot the DOM.
            await page.wait_for_timeout(1500)
            html = await page.content()
            return html, None
        except Exception as exc:  # pragma: no cover
            return None, f"Could not load seat page: {exc}"
        finally:  # pragma: no cover
            await page.close()

    async def _verify_one(self, chain: str, url: str) -> SeatMapParseResult:
        now = time.time()
        cached = self._cache.get(url)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        if not _robots_allows(url):
            result = SeatMapParseResult(None, reason="Blocked by robots.txt")
        else:
            await self._limiter.acquire()
            html, reason = await self._get_html(url)
            if html is None:
                result = SeatMapParseResult(None, reason=reason)
            else:
                result = parse_seat_html(chain, html)

        self._cache[url] = (now, result)
        return result

    # --- public entry point ------------------------------------------------ #
    async def enrich(
        self, showtimes: list[Showtime], seats_together: int, min_row: int
    ) -> tuple[int, list[str]]:
        """Upgrade eligible "check_manually" showtimes in place. Returns
        (number_verified, notes)."""
        cap = get_settings().seat_verification_max
        candidates = [
            st for st in showtimes
            if st.seat_check.status == "check_manually"
            and st.booking_url
            and st.chain in SUPPORTED_CHAINS
        ]
        notes: list[str] = []
        if not candidates:
            return 0, notes
        if len(candidates) > cap:
            notes.append(
                f"Seat verification capped at {cap} showtimes ({len(candidates)} eligible); "
                "the rest remain 'check manually'."
            )
            candidates = candidates[:cap]

        verified = 0
        try:
            for st in candidates:
                result = await self._verify_one(st.chain, st.booking_url)
                if result.ok:
                    st.seat_check = evaluate_rows(
                        result.rows, st.chain, st.theater_id, seats_together, min_row
                    )
                    verified += 1
                else:
                    # Keep it "check manually" but surface why verification didn't stick.
                    st.seat_check.reason = result.reason or st.seat_check.reason
        finally:
            await self._close()

        return verified, notes
