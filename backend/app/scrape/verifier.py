"""Playwright-based seat verification.

Takes showtimes we already have (from SerpApi etc.) and UPGRADES a "check
manually" badge into a real match / no-match with the physical row shown. It
never rediscovers showtimes, which sidesteps the most fragile part of scraping
and delivers the actual value: seats_together + min_row honored against a real
seat map.

Two problems had to be solved before this could work at all, both confirmed
against live pages on 2026-07-29:

1. **There was nothing to open.** SerpApi's per-showing ``link`` is a
   ``google.com/search`` URL, not a seat page. ``scrape.resolver`` now resolves a
   showtime to the chain's own seat URL by reading the chain's showtimes listing
   (one listing load per theater/date, cached and shared across showtimes).

2. **One parser could not cover the chains.** They differ fundamentally, so
   extraction is per-chain by strategy (see scrape_selectors.json):

   * ``cinemark`` → ``dom``: explicit ``button[available="True|False"]`` inside
     ``.seatRow``, with real labels. Parsed by the selector engine in seatmap.py.
   * ``amc`` → ``geometry``: no seat attributes and no text at all (labels are
     SVG glyph paths), so seats are recovered from layout geometry and resolved
     gradient fills. See scrape.geometry.
   * ``regal`` → ``blocked``: its seat page is behind a Cloudflare CAPTCHA.
     Solving that is out of bounds, so we report why and leave the badge alone.

Safety / politeness:
  * off unless ENABLE_SEAT_VERIFICATION=true and Playwright is installed
  * respects robots.txt, rate-limited, capped per search
  * per-URL TTL cache
  * import-safe (Playwright imported lazily)

Testability: the whole browser boundary is the single ``_fetch_page`` seam, so
resolution and parsing can be unit-tested by monkeypatching it.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from ..config import get_settings
from ..providers.scraper_provider import (
    RateLimiter,
    _robots_allows,
    _robots_cache,
    cache_robots,
    robots_root,
)
from ..schemas import SeatCheck, Showtime
from ..services.seatcheck import evaluate_rows
from ..services.theaters import load_theaters
from . import resolver
from .geometry import EXTRACT_JS, rows_from_extraction
from .seatmap import SeatMapParseResult, _chain_cfg, parse_seat_html

logger = logging.getLogger("showtime_finder.verifier")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 showtime-finder/1.0"
)


def _strip_html(rendered: str) -> Optional[str]:
    """Recover plain text from a browser-rendered text/plain document.

    Chromium wraps ``text/plain`` bodies in ``<html><body><pre>...</pre></body>``,
    so robots.txt comes back as markup. Returns None if there's no text to use.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(rendered or "", "html.parser")
    pre = soup.find("pre")
    text = pre.get_text() if pre else soup.get_text("\n")
    text = (text or "").strip()
    return text or None


def _strategy(chain: str) -> str:
    cfg = _chain_cfg(chain) or {}
    return str(cfg.get("strategy") or "dom").lower()


def verifiable_chains() -> set[str]:
    """Chains we can actually verify — i.e. configured and not `blocked`."""
    from .seatmap import _load_selectors

    return {
        name
        for name, cfg in (_load_selectors().get("chains") or {}).items()
        if str(cfg.get("strategy") or "dom").lower() != "blocked"
    }


# Kept as a module attribute for callers/tests that import it by name.
SUPPORTED_CHAINS = {"amc", "cinemark"}


class SeatVerifier:
    def __init__(self) -> None:
        s = get_settings()
        self._limiter = RateLimiter(s.scrape_rate_limit_per_sec)
        self._cache_ttl = s.seat_verification_cache_ttl_sec
        self._cache: dict[str, tuple[float, SeatMapParseResult]] = {}
        # (chain, slug, date) -> listing HTML, so N showtimes at one theater on
        # one date cost a single listing load.
        self._listing_cache: dict[tuple[str, str, str], tuple[float, Optional[str]]] = {}
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

    # --- the one browser seam (monkeypatched in tests) --------------------- #
    async def _fetch_page(
        self, url: str, cfg: dict, *, extract: bool = False
    ) -> tuple[Optional[str], Optional[dict], Optional[str]]:
        """Render ``url``; return (html, extraction, reason).

        ``extraction`` is the in-page geometry payload, present only when
        ``extract`` is True. On failure html is None and reason explains why.
        """
        # pragma: no cover below — exercised only with a live browser.
        await self._ensure_browser()  # pragma: no cover
        page = await self._context.new_page()  # pragma: no cover
        try:  # pragma: no cover
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # AMC fronts the whole site with a Queue-it waiting room; it clears on
            # its own, so wait it out rather than trying to route around it.
            queue_sub = cfg.get("queue_url_substring")
            if queue_sub and queue_sub in page.url:
                deadline = time.time() + float(cfg.get("queue_max_wait_sec") or 45)
                while queue_sub in page.url and time.time() < deadline:
                    await page.wait_for_timeout(2000)
                if queue_sub in page.url:
                    return None, None, "Chain queue/waiting room did not clear in time"

            # Let the SPA settle, and wait out skeleton placeholders if configured.
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            loading = cfg.get("loading_selector")
            if loading:
                try:
                    await page.wait_for_selector(loading, state="detached", timeout=10000)
                except Exception:
                    pass
            await page.wait_for_timeout(1200)

            data = None
            if extract:
                # Poll until the seat map has actually painted. Load-state events
                # are not a reliable signal here: on AMC, "networkidle" sometimes
                # resolves seconds before the SVG seats exist, and extracting then
                # yields zero seats — which looks identical to "markup changed".
                # Waiting for the seats themselves removes that race.
                want = int(cfg.get("min_seats_expected") or 1)
                deadline = time.time() + float(cfg.get("seat_wait_max_sec") or 20)
                while True:
                    data = await page.evaluate(EXTRACT_JS, cfg)
                    found = int(((data or {}).get("stats") or {}).get("seats_found") or 0)
                    if found >= want or time.time() >= deadline:
                        break
                    await page.wait_for_timeout(1000)

            html = await page.content()
            return html, data, None
        except Exception as exc:  # pragma: no cover
            return None, None, f"Could not load seat page: {exc}"
        finally:  # pragma: no cover
            await page.close()

    # --- robots ------------------------------------------------------------ #
    async def _robots_ok(self, url: str) -> tuple[bool, Optional[str]]:
        """Check robots.txt, reading it through the browser when plain HTTP can't.

        amctheatres.com answers ``/robots.txt`` with an HTML bot-check page to
        non-browser clients, so a plain fetch cannot see the real rules. Since we
        are already driving a browser for the pages themselves, we use it to read
        the published file too — then obey it. Never a way around a rule, only a
        way to actually read it.

        Returns (allowed, reason) so callers can distinguish "the site forbids
        this path" from "we could not read the rules and so declined".
        """
        root = robots_root(url)
        readable = True
        if root not in _robots_cache:
            html, _, _ = await self._fetch_page(f"{root}/robots.txt", {})
            body = _strip_html(html) if html else None
            readable = bool(body) and cache_robots(root, body)
        if _robots_allows(url):
            return True, None
        if not readable:
            return False, (
                f"Could not read robots.txt for {root} (the host returned a bot "
                "check instead), so scraping was declined rather than guessed at"
            )
        return False, None

    # --- seat URL resolution ---------------------------------------------- #
    def _theater_slug(self, theater_id: str) -> str:
        for t in load_theaters():
            if t.id == theater_id:
                return t.chain_slug
        return ""

    async def _resolve_seat_url(
        self, chain: str, theater_id: str, start: datetime, cfg: dict
    ) -> tuple[Optional[str], Optional[str]]:
        """Find the chain's seat URL for a showtime. Returns (url, reason)."""
        if not resolver.supports(chain):
            return None, f"No seat-URL resolver for chain '{chain}'"
        slug = self._theater_slug(theater_id)
        if not slug:
            return None, f"No chain_slug in theaters.json for '{theater_id}'"

        listing = resolver.listing_url(chain, slug, start)
        if not listing:
            return None, "Could not build a chain listing URL"

        key = (chain, slug, start.strftime("%Y-%m-%d"))
        now = time.time()
        hit = self._listing_cache.get(key)
        if hit and (now - hit[0]) < self._cache_ttl:
            html = hit[1]
        else:
            allowed, why = await self._robots_ok(listing)
            if not allowed:
                return None, why or "The chain's robots.txt disallows its showtimes listing"
            await self._limiter.acquire()
            html, _, reason = await self._fetch_page(listing, cfg)
            if html is None:
                return None, reason or "Could not load the chain showtimes listing"
            self._listing_cache[key] = (now, html)

        url = resolver.resolve_from_listing(chain, html or "", start, base=listing)
        if not url:
            return None, "No matching showtime found on the chain's listing page"
        return url, None

    # --- extraction -------------------------------------------------------- #
    async def _verify_one(
        self, chain: str, theater_id: str, start: datetime
    ) -> SeatMapParseResult:
        cfg = _chain_cfg(chain)
        if not cfg:
            return SeatMapParseResult(None, reason=f"No seat-map config for chain '{chain}'")

        strategy = _strategy(chain)
        if strategy == "blocked":
            return SeatMapParseResult(
                None,
                reason=cfg.get("blocked_reason")
                or f"Seat verification is not possible for chain '{chain}'",
            )

        seat_url, reason = await self._resolve_seat_url(chain, theater_id, start, cfg)
        if not seat_url:
            return SeatMapParseResult(None, reason=reason)

        now = time.time()
        cached = self._cache.get(seat_url)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        allowed, why = await self._robots_ok(seat_url)
        if not allowed:
            result = SeatMapParseResult(
                None,
                reason=why or "The chain's robots.txt disallows its seat-map page",
            )
        else:
            await self._limiter.acquire()
            html, extraction, why = await self._fetch_page(
                seat_url, cfg, extract=(strategy == "geometry")
            )
            if html is None:
                result = SeatMapParseResult(None, reason=why)
            elif strategy == "geometry":
                result = rows_from_extraction(extraction, cfg, page_text=html)
            else:
                result = parse_seat_html(chain, html)

        self._cache[seat_url] = (now, result)
        return result

    async def verify_showtime(
        self, chain: str, theater_id: str, start: datetime, seats_together: int, min_row: int
    ) -> tuple[SeatCheck, SeatMapParseResult]:
        """Verify one showtime on demand. Returns (seat_check, raw result)."""
        try:
            result = await self._verify_one(chain, theater_id, start)
        finally:
            await self._close()
        if result.ok:
            check = evaluate_rows(result.rows, chain, theater_id, seats_together, min_row)
        else:
            check = SeatCheck(
                status="check_manually",
                seats_together_requested=seats_together,
                min_row_requested=min_row,
                reason=result.reason,
            )
        return check, result

    # --- public entry point ------------------------------------------------ #
    async def enrich(
        self, showtimes: list[Showtime], seats_together: int, min_row: int
    ) -> tuple[int, list[str]]:
        """Upgrade eligible "check_manually" showtimes in place. Returns
        (number_verified, notes)."""
        cap = get_settings().seat_verification_max
        ok_chains = verifiable_chains()
        candidates = [
            st for st in showtimes
            if st.seat_check.status == "check_manually" and st.chain in ok_chains
        ]
        notes: list[str] = []

        skipped = {
            st.chain for st in showtimes
            if st.seat_check.status == "check_manually" and st.chain not in ok_chains
        }
        for chain in sorted(c for c in skipped if c):
            cfg = _chain_cfg(chain) or {}
            if cfg.get("blocked_reason"):
                notes.append(cfg["blocked_reason"])

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
                result = await self._verify_one(st.chain, st.theater_id, st.start_datetime)
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
