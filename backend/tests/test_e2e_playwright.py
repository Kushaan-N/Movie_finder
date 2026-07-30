"""Real end-to-end enrichment: actual Chromium renders locally-served pages, the
verifier resolves seat URLs from a listing, parses the maps, and upgrades badges.

Only the listing host is swapped (localhost fixtures instead of a chain's site) —
resolution, rendering, extraction, row normalization and the seat check are all
real. Skipped automatically if Playwright/Chromium isn't installed.

This deliberately covers every strategy at once, because they answer in different
ways: AMC recovers seats from SVG geometry, Regal can only prove sold-out state
from its listing, and Cinemark's seat map is policy-disabled.
"""
import asyncio
import importlib.util
from datetime import datetime

import pytest

from app.config import get_settings
from app.schemas import SeatCheck, Showtime
from app.scrape import resolver
from app.scrape.verifier import SeatVerifier
from tests._fixture_server import serve_fixtures

_HAS_PW = importlib.util.find_spec("playwright") is not None

# The AMC and Cinemark listing fixtures expose an 11:00pm showing.
START = datetime(2026, 8, 1, 23, 0)
# The Regal fixture's sold-out showing for The Odyssey is 10:30pm.
REGAL_SOLD_OUT = datetime(2026, 8, 1, 22, 30)
NO_SUCH_START = datetime(2026, 8, 1, 4, 5)  # nothing in the listings is near this

_LISTING_PATH = {
    "cinemark": "/cinemark-listing",
    "amc": "/amc-listing",
    "regal": "/regal-listing",
}


def _st(key, chain, theater_id, start=START):
    return Showtime(
        key=key,
        theater_id=theater_id,
        theater_name=f"{chain.upper()} Test",
        chain=chain,
        movie_title="The Odyssey",
        format="IMAX",
        start_datetime=start,
        start_time_label="11:00 PM",
        # Providers only ever give a google.com link; it must be irrelevant now.
        booking_url="https://www.google.com/search?q=irrelevant",
        seat_check=SeatCheck(status="check_manually", seats_together_requested=4, min_row_requested=5),
    )


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_real_browser_enrichment_across_strategies(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)  # keep the test fast

    with serve_fixtures() as base:
        monkeypatch.setattr(
            resolver, "listing_url",
            lambda chain, slug, day: (
                f"{base}{_LISTING_PATH[chain]}" if chain in _LISTING_PATH else None
            ),
        )
        showtimes = [
            _st("amc", "amc", "amc-metreon-16"),
            # 10:30pm is sold out for The Odyssey in the Regal listing fixture.
            _st("regal", "regal", "regal-hacienda-crossings", start=REGAL_SOLD_OUT),
            _st("cinemark", "cinemark", "cinemark-century-20-oakridge"),
            _st("miss", "amc", "amc-metreon-16", start=NO_SUCH_START),
        ]
        verifier = SeatVerifier()
        assert verifier.available(), "seat verification should be available with Playwright installed"

        verified, notes = asyncio.run(verifier.enrich(showtimes, seats_together=4, min_row=1))

    assert verified == 1  # only AMC yields a real seat map
    by_key = {st.key: st for st in showtimes}

    # AMC (geometry strategy): run of 4 in the last of 3 rows.
    assert by_key["amc"].seat_check.status == "match"
    assert by_key["amc"].seat_check.best_block_row.physical_row == 3
    assert by_key["amc"].seat_check.best_block_size == 4

    # Regal (capacity strategy): sold out is a definitive no, not an unknown.
    assert by_key["regal"].seat_check.status == "no_match"
    assert "sold out" in (by_key["regal"].seat_check.reason or "").lower()
    assert any("sold out" in n.lower() for n in notes)

    # Cinemark: robots.txt forbids its seat map, and that is said plainly.
    assert by_key["cinemark"].seat_check.status == "check_manually"
    assert any("robots.txt" in n.lower() for n in notes)

    # A showtime absent from the listing never fabricates a match.
    assert by_key["miss"].seat_check.status == "check_manually"


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_real_browser_honors_min_row(monkeypatch):
    """The whole point of the feature: a physical-row floor actually filters."""
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)

    with serve_fixtures() as base:
        monkeypatch.setattr(
            resolver, "listing_url", lambda chain, slug, day: f"{base}{_LISTING_PATH[chain]}"
        )
        sts = [_st("amc", "amc", "amc-metreon-16")]
        asyncio.run(SeatVerifier().enrich(sts, seats_together=4, min_row=4))

    # The map's only qualifying block sits at physical row 3.
    assert all(st.seat_check.status == "no_match" for st in sts)
