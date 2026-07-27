"""Seat-verification enrichment wiring (no real browser — the network seam is
monkeypatched to return saved HTML)."""
import asyncio
import os
from datetime import datetime

from app.schemas import SeatCheck, Showtime
from app.scrape import verifier as verifier_mod
from app.scrape.verifier import SeatVerifier

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _amc_html() -> str:
    with open(os.path.join(FIXTURES, "amc_seatmap.html"), encoding="utf-8") as f:
        return f.read()


def _showtime(chain="amc", url="https://www.amctheatres.com/showtimes/x/seats", status="check_manually"):
    return Showtime(
        key="k1",
        theater_id="amc-metreon-16",
        theater_name="AMC Metreon 16",
        chain=chain,
        movie_title="Dune",
        format="IMAX",
        start_datetime=datetime(2026, 7, 27, 19, 15),
        start_time_label="7:15 PM",
        booking_url=url,
        seat_check=SeatCheck(status=status, seats_together_requested=4, min_row_requested=5),
    )


def _patch_network(monkeypatch, html=None, reason=None):
    async def fake_get_html(self, url):
        return html, reason
    monkeypatch.setattr(SeatVerifier, "_get_html", fake_get_html)
    # Don't hit the network for robots.txt in tests.
    monkeypatch.setattr(verifier_mod, "_robots_allows", lambda url: True)


def test_enrich_upgrades_check_manually_to_match(monkeypatch):
    _patch_network(monkeypatch, html=_amc_html())
    st = _showtime()
    verified, notes = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=5))
    assert verified == 1
    assert st.seat_check.status == "match"
    # Row H (physical row 8) holds the qualifying block.
    assert st.seat_check.best_block_row.physical_row == 8
    assert st.seat_check.best_block_size == 5


def test_enrich_respects_min_row(monkeypatch):
    _patch_network(monkeypatch, html=_amc_html())
    st = _showtime()
    # Require a block at/behind physical row 9 — the only 4+ block is at row 8.
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=9))
    assert st.seat_check.status == "no_match"


def test_enrich_keeps_check_manually_when_unparseable(monkeypatch):
    _patch_network(monkeypatch, html="<canvas></canvas>")
    st = _showtime()
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=5))
    assert verified == 0
    assert st.seat_check.status == "check_manually"
    assert "canvas" in (st.seat_check.reason or "").lower()


def test_enrich_skips_unsupported_chain(monkeypatch):
    _patch_network(monkeypatch, html=_amc_html())
    st = _showtime(chain="alamo")  # not in SUPPORTED_CHAINS
    verified, _ = asyncio.run(SeatVerifier().enrich([st], seats_together=4, min_row=5))
    assert verified == 0
    assert st.seat_check.status == "check_manually"


def test_enrich_caps_and_notes(monkeypatch):
    _patch_network(monkeypatch, html=_amc_html())
    monkeypatch.setattr(verifier_mod.get_settings(), "seat_verification_max", 2, raising=False)
    sts = [_showtime(url=f"https://www.amctheatres.com/s/{i}/seats") for i in range(5)]
    verified, notes = asyncio.run(SeatVerifier().enrich(sts, seats_together=4, min_row=5))
    assert verified == 2
    assert any("capped" in n for n in notes)
