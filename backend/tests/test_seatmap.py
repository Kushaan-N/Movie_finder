"""Seat-map HTML parser: DOM order, availability, wall/canvas safety, fallbacks."""
import os

from app.scrape.seatmap import parse_seat_html

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_amc_fixture_parses_in_dom_order():
    res = parse_seat_html("amc", _fixture("amc_seatmap.html"))
    assert res.ok
    assert [r.raw_label for r in res.rows] == list("ABCDEFGH")
    # Row H (DOM index 7) has 5 available then a companion (unavailable).
    assert res.rows[-1].seats_available == [True, True, True, True, True, False]


def test_space_breaks_contiguity():
    res = parse_seat_html("amc", _fixture("amc_seatmap.html"))
    # Row A: sold, sold, SPACE, available, sold -> the space is a False gap.
    assert res.rows[0].seats_available == [False, False, False, True, False]


def test_companion_and_sold_are_unavailable():
    res = parse_seat_html("amc", _fixture("amc_seatmap.html"))
    assert res.stats["available_found"] == 15
    assert res.stats["seats_found"] == 26


def test_canvas_rendering_is_check_manually_not_a_guess():
    html = '<div class="seatMapContainer"><canvas id="seatmap"></canvas></div>'
    res = parse_seat_html("amc", html)
    assert res.rows is None
    assert "canvas" in res.reason.lower()


def test_login_wall_by_text():
    html = '<div class="seatMapRow"><p>Please sign in to continue to seat selection.</p></div>'
    res = parse_seat_html("amc", html)
    assert res.rows is None
    assert "wall" in res.reason.lower()


def test_login_wall_by_selector():
    html = '<div class="authWall">Members only</div>'
    res = parse_seat_html("amc", html)
    assert res.rows is None
    assert "wall" in res.reason.lower()


def test_unrecognized_status_declines_rather_than_guessing():
    # Seats exist but carry a status value the config doesn't know -> decline.
    html = (
        '<div class="seatMapRow" data-row-name="A">'
        '<div class="seat" data-seat-status="mysterystate"></div>'
        '<div class="seat" data-seat-status="mysterystate"></div>'
        "</div>"
    )
    res = parse_seat_html("amc", html)
    assert res.rows is None
    assert "availability" in res.reason.lower()


def test_flat_layout_grouped_by_row_attr():
    # No row containers; seats carry data-row-name. Regal config groups them.
    html = (
        '<div id="map">'
        '<span class="seat" data-row="1" data-status="available"></span>'
        '<span class="seat" data-row="1" data-status="taken"></span>'
        '<span class="seat" data-row="2" data-status="available"></span>'
        '<span class="seat" data-row="2" data-status="available"></span>'
        "</div>"
    )
    res = parse_seat_html("regal", html)
    assert res.ok
    assert [r.raw_label for r in res.rows] == ["1", "2"]
    assert res.rows[1].seats_available == [True, True]


def test_unknown_chain_has_no_selectors():
    res = parse_seat_html("nickelodeon", "<div class='seat'></div>")
    assert res.rows is None
    assert "no seat-map selectors" in res.reason.lower()


def test_empty_html():
    res = parse_seat_html("amc", "")
    assert res.rows is None
