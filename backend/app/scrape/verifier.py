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

   * ``amc`` → ``geometry``: no seat attributes and no text at all (labels are
     SVG glyph paths), so seats are recovered from layout geometry and resolved
     gradient fills. See scrape.geometry. This is the only chain that yields a
     full seat map.
   * ``regal`` → ``capacity``: its seat page is behind a Cloudflare CAPTCHA, so
     the exact map is unreachable. Its listing IS reachable and publishes
     sold-out state, which settles the seat question for the shows it marks —
     zero seats cannot seat any group — so those become a real no-match instead
     of "check manually".
   * ``cinemark`` → ``dom`` but ``disabled``: its markup is the cleanest of the
     three, yet robots.txt disallows /TicketSeatMap, so we must not fetch it. The
     parser stays verified and ready if that policy changes.

Neither Regal nor Cinemark can be "fixed" by better parsing: one is a CAPTCHA and
the other is the site's stated crawling policy. Both are reported as such rather
than worked around.

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


def _unavailable_reason(cfg: dict) -> Optional[str]:
    """Why a chain cannot be verified at all, if that's the case.

    Two distinct cases, both the chain's decision rather than a parser gap:
    ``disabled`` means the site forbids fetching its seat map (Cinemark's
    robots.txt), and the ``blocked`` strategy means it cannot be reached
    (Regal's CAPTCHA — though Regal still yields sold-out state, see the
    ``capacity`` strategy).
    """
    if cfg.get("disabled"):
        return cfg.get("disabled_reason") or "Seat verification is disabled for this chain."
    if str(cfg.get("strategy") or "dom").lower() == "blocked":
        return cfg.get("blocked_reason") or "Seat verification is not possible for this chain."
    return None


def verifiable_chains() -> set[str]:
    """Chains that can produce a real seat answer — full map or sold-out state."""
    from .seatmap import _load_selectors

    return {
        name
        for name, cfg in (_load_selectors().get("chains") or {}).items()
        if _unavailable_reason(cfg) is None
    }


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
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

    async def _close(self):  # pragma: no cover - needs a real browser
        for closer in (
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer:
                try:
                    await closer()
                except Exception:
                    pass
        self._pw = self._browser = None

    # --- the one browser seam (monkeypatched in tests) --------------------- #
    async def _fetch_page(
        self, url: str, cfg: dict, *, extract: bool = False
    ) -> tuple[Optional[str], Optional[dict], Optional[str]]:
        """Render ``url``; return (html, extraction, reason).

        ``extraction`` is the in-page geometry payload, present only when
        ``extract`` is True. On failure html is None and reason explains why.
        """
        # Each load gets its OWN browser context, and that is a correctness fix
        # rather than hygiene: AMC serves 403 for the SPA's JS chunks on a second
        # navigation from the same context, so the seat page arrived as an unbooted
        # shell (empty <title>, 3 svgs) and looked like "the map didn't load".
        # Reproduced against the live site; a fresh context renders all 198 seats.
        # Contexts are cheap and the browser stays shared.
        # pragma: no cover below — exercised only with a live browser.
        await self._ensure_browser()  # pragma: no cover
        context = await self._browser.new_context(user_agent=_USER_AGENT)  # pragma: no cover
        page = await context.new_page()  # pragma: no cover
        try:  # pragma: no cover
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # AMC fronts the whole site with a Queue-it waiting room; it clears on
            # its own, so wait it out rather than trying to route around it.
            #
            # The redirect into the queue does not always land before
            # domcontentloaded, so checking once here missed it: we then polled a
            # queue page for seats, found none, and reported "page structure changed"
            # for what was really "we never got to the map". Observed live. The
            # check therefore re-runs until the URL settles off the queue.
            queue_sub = cfg.get("queue_url_substring")
            if queue_sub:
                deadline = time.time() + float(cfg.get("queue_max_wait_sec") or 45)
                while time.time() < deadline:
                    if queue_sub in page.url:
                        await page.wait_for_timeout(2000)
                        continue
                    # Off the queue — give a late redirect a moment to appear.
                    await page.wait_for_timeout(1000)
                    if queue_sub not in page.url:
                        break
                if queue_sub in page.url:
                    return None, None, (
                        "The chain's queue/waiting room did not clear in time — "
                        "it is under load; try again shortly"
                    )

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
            try:
                await page.close()
            finally:
                await context.close()

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

    async def _get_listing(
        self, chain: str, theater_id: str, start: datetime, cfg: dict
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Fetch the chain's showtimes listing for a theater/date.

        Returns (html, listing_url, reason). Cached per (chain, slug, date) so N
        showtimes at one theater on one date cost a single load.
        """
        slug = self._theater_slug(theater_id)
        if not slug:
            return None, None, f"No chain_slug in theaters.json for '{theater_id}'"

        listing = resolver.listing_url(chain, slug, start)
        if not listing:
            return None, None, "Could not build a chain listing URL"

        key = (chain, slug, start.strftime("%Y-%m-%d"))
        now = time.time()
        hit = self._listing_cache.get(key)
        if hit and (now - hit[0]) < self._cache_ttl:
            return hit[1], listing, None

        allowed, why = await self._robots_ok(listing)
        if not allowed:
            return None, listing, (
                why or "The chain's robots.txt disallows its showtimes listing"
            )
        await self._limiter.acquire()
        html, _, reason = await self._fetch_page(listing, cfg)
        if html is None:
            return None, listing, reason or "Could not load the chain showtimes listing"
        self._listing_cache[key] = (now, html)
        return html, listing, None

    async def _resolve_seat_url(
        self, chain: str, theater_id: str, start: datetime, cfg: dict,
        movie_title: str = "",
    ) -> tuple[Optional[str], Optional[str]]:
        """Find the chain's seat URL for a showtime. Returns (url, reason)."""
        if not resolver.supports(chain):
            return None, f"No seat-URL resolver for chain '{chain}'"
        html, listing, reason = await self._get_listing(chain, theater_id, start, cfg)
        if html is None:
            return None, reason

        url = resolver.resolve_from_listing(
            chain, html or "", start, base=listing, movie_title=movie_title
        )
        if not url:
            named = f" for '{movie_title}'" if movie_title else ""
            return None, (
                f"No showtime{named} at that time on the chain's listing page"
            )
        return url, None

    # --- extraction -------------------------------------------------------- #
    async def _verify_one(
        self, chain: str, theater_id: str, start: datetime, movie_title: str = ""
    ) -> SeatMapParseResult:
        cfg = _chain_cfg(chain)
        if not cfg:
            return SeatMapParseResult(None, reason=f"No seat-map config for chain '{chain}'")

        unavailable = _unavailable_reason(cfg)
        if unavailable:
            return SeatMapParseResult(None, reason=unavailable)

        strategy = _strategy(chain)
        if strategy == "capacity":
            # The seat map is unreachable, but the listing publishes sold-out
            # state, which settles the seat question for the one case it proves.
            html, _, reason = await self._get_listing(chain, theater_id, start, cfg)
            if html is None:
                return SeatMapParseResult(None, reason=reason)
            sold = resolver.find_sold_out(chain, html, movie_title, start)
            if sold is True:
                return SeatMapParseResult(
                    None,
                    sold_out=True,
                    reason=cfg.get("sold_out_reason")
                    or "This showtime is sold out, so no seats are available.",
                )
            if sold is False:
                return SeatMapParseResult(
                    None,
                    reason=cfg.get("not_sold_out_reason")
                    or (
                        "Seats are still on sale, but the exact seat map can't be "
                        "read for this chain."
                    ),
                )
            return SeatMapParseResult(
                None,
                reason=cfg.get("blocked_reason")
                or f"Seat verification is not possible for chain '{chain}'",
            )

        seat_url, reason = await self._resolve_seat_url(
            chain, theater_id, start, cfg, movie_title
        )
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

        # Carry the resolved page back regardless of the parse outcome: finding it
        # is the expensive step, and it is exactly the page the user would
        # otherwise have to hunt for -- especially useful when parsing failed.
        result.seat_url = seat_url
        self._cache[seat_url] = (now, result)
        return result

    @staticmethod
    def _check_from(
        result: SeatMapParseResult, chain: str, theater_id: str,
        seats_together: int, min_row: int,
    ) -> SeatCheck:
        """Turn a parse result into a seat check, including the sold-out shortcut."""
        if result.ok:
            return evaluate_rows(result.rows, chain, theater_id, seats_together, min_row)
        if result.sold_out:
            # Zero seats available is a real answer, not an unknown.
            return SeatCheck(
                status="no_match",
                seats_together_requested=seats_together,
                min_row_requested=min_row,
                best_block_size=None,
                reason=result.reason,
            )
        return SeatCheck(
            status="check_manually",
            seats_together_requested=seats_together,
            min_row_requested=min_row,
            reason=result.reason,
        )

    async def verify_showtime(
        self, chain: str, theater_id: str, start: datetime, seats_together: int,
        min_row: int, movie_title: str = "",
    ) -> tuple[SeatCheck, SeatMapParseResult]:
        """Verify one showtime on demand. Returns (seat_check, raw result)."""
        try:
            result = await self._verify_one(chain, theater_id, start, movie_title)
        finally:
            await self._close()
        return self._check_from(result, chain, theater_id, seats_together, min_row), result

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
            why = _unavailable_reason(_chain_cfg(chain) or {})
            if why:
                notes.append(why)

        if not candidates:
            return 0, notes
        if len(candidates) > cap:
            notes.append(
                f"Seat verification capped at {cap} showtimes ({len(candidates)} eligible); "
                "the rest remain 'check manually'."
            )
            candidates = candidates[:cap]

        verified = sold_out = 0
        try:
            for st in candidates:
                result = await self._verify_one(
                    st.chain, st.theater_id, st.start_datetime, st.movie_title
                )
                if result.ok or result.sold_out:
                    st.seat_check = self._check_from(
                        result, st.chain, st.theater_id, seats_together, min_row
                    )
                    if result.sold_out:
                        sold_out += 1
                    else:
                        verified += 1
                else:
                    # Keep it "check manually" but surface why verification didn't stick.
                    st.seat_check.reason = result.reason or st.seat_check.reason
        finally:
            await self._close()

        if sold_out:
            notes.append(
                f"{sold_out} showtime(s) are sold out per the chain's listing, so they "
                "cannot seat your group."
            )
        return verified, notes
