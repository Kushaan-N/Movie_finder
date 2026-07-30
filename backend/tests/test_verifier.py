"""Seat-verification wiring: URL resolution, per-chain strategies, enrichment.

No real browser — the single ``_fetch_page`` seam is monkeypatched. Fixtures
mirror markup verified against live pages on 2026-07-29, so a passing test here
means the parser matches what the chains actually serve.
"""
import asyncio
import os
from datetime import datetime

import pytest

from app.schemas import SeatCheck, Showtime
from app.scrape import verifier as verifier_mod
from app.scrape.verifier import SeatVerifier, verifiable_chains

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
START = datetime(2026, 8, 1, 23, 0)  # matches the 11:00pm link in the listing


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def cinemark_enabled(monkeypatch):
    """Re-enable Cinemark for tests of the `dom` strategy.

    Its parser is verified against real markup; it is disabled in production only
    because robots.txt forbids fetching the seat map. Keeping it exercised means
    the strategy still works the day that policy changes.
    """
    from app.scrape.seatmap import _load_selectors

    cfg = _load_selectors()["chains"]["cinemark"]
    monkeypatch.delitem(cfg, "disabled", raising=False)
    return cfg


def _showtime(chain="cinemark", theater_id="cinemark-century-20-oakridge",
              status="check_manually", start=START, key="k1"):
    return Showtime(
        key=key,
        theater_id=theater_id,
        theater_name="Century 20 Oakridge and XD",
        chain=chain,
        movie_title="The Odyssey",
        format="XD",
        start_datetime=start,
        start_time_label="11:00 PM",
        booking_url="https://www.google.com/search?q=irrelevant",
        seat_check=SeatCheck(status=status, seats_together_requested=4, min_row_requested=5),
    )


def _patch(monkeypatch, *, seat_html=None, extraction=None, reason=None,
           listing_html=None):
    """Patch the browser seam. Listing requests and seat requests are distinguished
    by URL, mirroring the real two-step flow (resolve URL, then read seats)."""

    async def fake_fetch(self, url, cfg, extract=False):
        if reason:
            return None, None, reason
        if "TicketSeatMap" in url or "/seats" in url:
            return seat_html, extraction, None
        if listing_html is not None:
            return listing_html, None, None
        # Serve each chain its own listing markup.
        if "amctheatres" in url:
            name = "amc_listing.html"
        elif "regmovies" in url:
            name = "regal_listing.html"
        else:
            name = "cinemark_listing.html"
        return _fixture(name), None, None

    monkeypatch.setattr(SeatVerifier, "_fetch_page", fake_fetch)
    monkeypatch.setattr(verifier_mod, "_robots_allows", lambda url: True)


# --- chain support ---------------------------------------------------------- #

def test_only_chains_that_can_answer_are_verifiable():
    """AMC yields a full map; Regal yields sold-out state; Cinemark is forbidden."""
    chains = verifiable_chains()
    assert "amc" in chains
    assert "regal" in chains          # capacity strategy still answers sold-out
    assert "cinemark" not in chains   # robots.txt disallows its seat map


def test_disabled_chain_reports_policy_instead_of_failing_silently(monkeypatch):
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"))
    st = _showtime(chain="cinemark")
    verified, notes = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert verified == 0
    assert st.seat_check.status == "check_manually"
    assert any("robots.txt" in n.lower() for n in notes)


# --- regal: capacity strategy ----------------------------------------------- #

def test_regal_sold_out_becomes_a_definitive_no_match(monkeypatch):
    """Zero seats is a real answer, not an unknown."""
    _patch(monkeypatch, seat_html="<html></html>")
    st = _showtime(chain="regal", theater_id="regal-hacienda-crossings",
                   start=datetime(2026, 8, 1, 22, 30))  # 10:30pm, sold out
    verified, notes = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert st.seat_check.status == "no_match"
    assert "sold out" in (st.seat_check.reason or "").lower()
    assert verified == 0  # counted separately from real seat-map verifications
    assert any("sold out" in n.lower() for n in notes)


def test_regal_on_sale_stays_check_manually_with_the_captcha_reason(monkeypatch):
    _patch(monkeypatch, seat_html="<html></html>")
    st = _showtime(chain="regal", theater_id="regal-hacienda-crossings",
                   start=datetime(2026, 8, 1, 21, 30))  # 9:30pm, still on sale
    asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert st.seat_check.status == "check_manually"
    assert "captcha" in (st.seat_check.reason or "").lower()


def test_regal_sold_out_is_attributed_to_the_right_film(monkeypatch):
    """A listing carries many movies at overlapping times.

    The fixture has The Odyssey on sale at 7:30pm while Spider-Man is sold out at
    7:30pm; matching on time alone would report the wrong film's state.
    """
    _patch(monkeypatch, seat_html="<html></html>")
    st = _showtime(chain="regal", theater_id="regal-hacienda-crossings",
                   start=datetime(2026, 8, 1, 19, 30))
    asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert st.seat_check.status == "check_manually"  # NOT no_match


# --- cinemark: dom strategy ------------------------------------------------- #

def test_cinemark_enrich_upgrades_to_match(monkeypatch, cinemark_enabled):
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"))
    st = _showtime()
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert verified == 1
    assert st.seat_check.status == "match"
    # Row C is DOM index 2 -> physical row 3, holding the run of 5.
    assert st.seat_check.best_block_size == 5
    assert st.seat_check.best_block_row.physical_row == 3


def test_cinemark_respects_min_row(monkeypatch, cinemark_enabled):
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"))
    st = _showtime()
    # The only 4+ run is at physical row 3; demanding row 4+ must not match.
    asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=4))
    assert st.seat_check.status == "no_match"


def test_cinemark_ignores_wheelchair_and_companion_positions(monkeypatch, cinemark_enabled):
    """Accessible positions are not general seating and must not pad a run."""
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"))
    st = _showtime()
    asyncio.run(SeatVerifier().enrich([st], seats_together=6, min_row=1))
    # Row C has 5 real seats + wheelchair + companion; a 6-run must fail.
    assert st.seat_check.status == "no_match"
    assert st.seat_check.best_block_size == 5


def test_canvas_map_stays_check_manually(monkeypatch, cinemark_enabled):
    _patch(monkeypatch, seat_html="<html><body><canvas></canvas></body></html>")
    st = _showtime()
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert verified == 0
    assert st.seat_check.status == "check_manually"
    assert "canvas" in (st.seat_check.reason or "").lower()


def test_unresolvable_showtime_explains_itself(monkeypatch, cinemark_enabled):
    # A listing with no matching time -> no seat URL to open.
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"),
           listing_html="<html><body>no showtimes here</body></html>")
    st = _showtime()
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert verified == 0
    assert "no matching showtime" in (st.seat_check.reason or "").lower()


# --- amc: geometry strategy ------------------------------------------------- #

def _amc_extraction(rows, unrecognized=0):
    flat = [s for r in rows for s in r]
    return {
        "rows": rows,
        "stats": {
            "seats_found": sum(1 for s in flat if not s.get("gap")),
            "available_found": sum(1 for s in flat if s.get("available")),
            "gaps": sum(1 for s in flat if s.get("gap")),
            "unrecognized": unrecognized,
            "canvas": 0,
        },
    }


def _seat(available=False, gap=False):
    """A seat cell. `gap` marks an aisle, which breaks contiguity."""
    return {"available": available, "gap": gap}


def test_amc_geometry_enrich_upgrades_to_match(monkeypatch):
    # 3 rows of 10; the third row has a run of 4 available.
    rows = [
        [_seat() for _ in range(10)],
        [_seat() for _ in range(10)],
        [_seat(available=True) if 2 <= i <= 5 else _seat() for i in range(10)],
    ]
    _patch(monkeypatch, seat_html="<html></html>", extraction=_amc_extraction(rows))
    st = _showtime(chain="amc", theater_id="amc-metreon-16")
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert verified == 1
    assert st.seat_check.status == "match"
    assert st.seat_check.best_block_size == 4
    assert st.seat_check.best_block_row.physical_row == 3


def test_amc_geometry_declines_when_palette_changes(monkeypatch):
    """Only the known seat palette counts as a seat, so a changed palette shows up
    as too few seats — never as a wrongly-empty auditorium."""
    rows = [[_seat(available=True) for _ in range(4)]]  # nearly everything unrecognized
    _patch(monkeypatch, seat_html="<html></html>",
           extraction=_amc_extraction(rows, unrecognized=190))
    st = _showtime(chain="amc", theater_id="amc-metreon-16")
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=2, min_row=1))
    assert verified == 0
    assert st.seat_check.status == "check_manually"
    assert "palette" in (st.seat_check.reason or "").lower()


def test_amc_geometry_declines_on_too_few_seats(monkeypatch):
    """A handful of stray svgs is not an auditorium — decline rather than guess."""
    rows = [[_seat(available=True) for _ in range(3)]]
    _patch(monkeypatch, seat_html="<html></html>", extraction=_amc_extraction(rows))
    st = _showtime(chain="amc", theater_id="amc-metreon-16")
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=2, min_row=1))
    assert verified == 0
    assert "expected at least" in (st.seat_check.reason or "").lower()


def test_amc_geometry_treats_aisles_as_breaking_contiguity(monkeypatch):
    """Seats either side of an aisle are not adjacent, so a run must not span it."""
    row = ([_seat(available=True)] * 4) + [_seat(gap=True)] + ([_seat(available=True)] * 4)
    rows = [[_seat() for _ in range(20)], row]
    _patch(monkeypatch, seat_html="<html></html>", extraction=_amc_extraction(rows))
    st = _showtime(chain="amc", theater_id="amc-metreon-16")
    asyncio.run(SeatVerifier().enrich([st], seats_together=8, min_row=1))
    assert st.seat_check.status == "no_match"
    assert st.seat_check.best_block_size == 4


# --- shared behavior -------------------------------------------------------- #

def test_enrich_caps_and_notes(monkeypatch, cinemark_enabled):
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"))
    monkeypatch.setattr(verifier_mod.get_settings(), "seat_verification_max", 2, raising=False)
    sts = [_showtime(key=f"k{i}") for i in range(5)]
    verified, notes = asyncio.run(SeatVerifier().enrich(sts, seats_together=4, min_row=1))
    assert verified == 2
    assert any("capped" in n for n in notes)


def test_enrich_skips_unknown_chain(monkeypatch):
    _patch(monkeypatch, seat_html=_fixture("cinemark_seatmap.html"))
    st = _showtime(chain="alamo")
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert verified == 0
    assert st.seat_check.status == "check_manually"


def test_listing_is_fetched_once_per_theater_and_date(monkeypatch, cinemark_enabled):
    """N showtimes at one theater on one date must cost one listing load."""
    calls: list[str] = []

    async def fake_fetch(self, url, cfg, extract=False):
        calls.append(url)
        if "TicketSeatMap" in url:
            return _fixture("cinemark_seatmap.html"), None, None
        return _fixture("cinemark_listing.html"), None, None

    monkeypatch.setattr(SeatVerifier, "_fetch_page", fake_fetch)
    monkeypatch.setattr(verifier_mod, "_robots_allows", lambda url: True)

    sts = [_showtime(key=f"k{i}") for i in range(3)]
    asyncio.run(SeatVerifier().enrich(sts, seats_together=4, min_row=1))
    listings = [u for u in calls if "TicketSeatMap" not in u]
    assert len(listings) == 1


def test_amc_geometry_excludes_page_chrome_from_rows(monkeypatch):
    """Page chrome must not become a row.

    Back/close/collapse icons are seat-sized; if they clustered into a phantom row
    above the map, every physical row number would shift and min_row would break
    silently. Only elements painted in the seat palette are collected, so chrome
    lands in `unrecognized` instead.
    """
    rows = [
        [_seat(available=True) for _ in range(12)],  # real row 1
        [_seat() for _ in range(12)],                # real row 2
    ]
    _patch(monkeypatch, seat_html="<html></html>",
           extraction=_amc_extraction(rows, unrecognized=10))
    st = _showtime(chain="amc", theater_id="amc-metreon-16")
    asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=1))
    assert st.seat_check.status == "match"
    # The open row is physical row 1 — chrome never entered the row list.
    assert st.seat_check.best_block_row.physical_row == 1
