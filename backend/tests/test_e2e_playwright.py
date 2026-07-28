"""Real end-to-end test: actual Chromium renders locally-served seat pages, the
verifier parses them, and enrichment upgrades the badges. Only the target host is
swapped (localhost fixtures instead of a chain's site) — every other moving part
is real. Skipped automatically if Playwright/Chromium isn't installed.
"""
import asyncio
import importlib.util
from datetime import datetime

import pytest

from app.config import get_settings
from app.schemas import SeatCheck, Showtime
from app.scrape.verifier import SeatVerifier
from tests._fixture_server import serve_fixtures

_HAS_PW = importlib.util.find_spec("playwright") is not None


def _st(key, chain, url):
    return Showtime(
        key=key,
        theater_id=f"{chain}-test",
        theater_name=f"{chain.upper()} Test",
        chain=chain,
        movie_title="Dune",
        format="IMAX",
        start_datetime=datetime(2026, 7, 28, 19, 15),
        start_time_label="7:15 PM",
        booking_url=url,
        seat_check=SeatCheck(status="check_manually", seats_together_requested=4, min_row_requested=5),
    )


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_real_browser_enrichment_all_chains(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)  # keep the test fast

    with serve_fixtures() as base:
        showtimes = [
            _st("amc", "amc", f"{base}/amc"),
            _st("regal", "regal", f"{base}/regal"),
            _st("cinemark", "cinemark", f"{base}/cinemark"),
            _st("miss", "amc", f"{base}/nope"),  # 404 -> no seat map -> stays check_manually
        ]
        verifier = SeatVerifier()
        assert verifier.available(), "seat verification should be available with Playwright installed"

        verified, _notes = asyncio.run(verifier.enrich(showtimes, seats_together=4, min_row=5))

    assert verified == 3
    by_key = {st.key: st for st in showtimes}
    # AMC: qualifying block at physical row 8.
    assert by_key["amc"].seat_check.status == "match"
    assert by_key["amc"].seat_check.best_block_row.physical_row == 8
    # Regal: physical row 6.
    assert by_key["regal"].seat_check.status == "match"
    assert by_key["regal"].seat_check.best_block_row.physical_row == 6
    # Cinemark: physical row 7.
    assert by_key["cinemark"].seat_check.status == "match"
    assert by_key["cinemark"].seat_check.best_block_row.physical_row == 7
    # The 404 URL never fabricates a match.
    assert by_key["miss"].seat_check.status == "check_manually"
