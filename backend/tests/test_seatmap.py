"""Selector-based seat-map parsing (the `dom` strategy).

Exercised against Cinemark, the one chain that actually serves a parseable
attribute contract. The previous version of this file tested invented AMC and
Regal markup that does not exist on either site — AMC renders seats as SVG paths
with no attributes (see scrape.geometry) and Regal's seat page is CAPTCHA-gated —
so those fixtures were deleted rather than left to imply coverage.
"""
import os

from app.scrape.seatmap import parse_seat_html

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def _cinemark():
    return parse_seat_html("cinemark", _fixture("cinemark_seatmap.html"))


def test_cinemark_fixture_parses_in_dom_order():
    res = _cinemark()
    assert res.ok
    assert [r.raw_label for r in res.rows] == ["A", "B", "C"]


def test_blank_position_breaks_contiguity():
    res = _cinemark()
    # Row A: A20 sold, A19 sold, .seatBlank gap, A18 open, A17 sold.
    assert res.rows[0].seats_available == [False, False, False, True, False]


def test_available_attribute_drives_state():
    res = _cinemark()
    assert res.rows[1].seats_available == [True, False, True]


def test_wheelchair_and_companion_positions_are_gaps():
    """They are not general seating, so they must not extend a contiguous run."""
    res = _cinemark()
    # Row C: five real open seats, then wheelchair + companion as gaps.
    assert res.rows[2].seats_available == [True, True, True, True, True, False, False]


def test_stats_count_only_real_seats():
    res = _cinemark()
    assert res.stats["seats_found"] == 12  # 4 + 3 + 5, gaps excluded
    assert res.stats["available_found"] == 8  # 1 + 2 + 5


def test_canvas_rendering_is_check_manually_not_a_guess():
    res = parse_seat_html("cinemark", '<div id="map"><canvas id="seatmap"></canvas></div>')
    assert res.rows is None
    assert "canvas" in res.reason.lower()


def test_login_wall_by_text():
    res = parse_seat_html(
        "cinemark", '<div class="seatRow"><p>Please sign in to select seats.</p></div>'
    )
    assert res.rows is None
    assert "wall" in res.reason.lower()


def test_login_wall_by_selector():
    res = parse_seat_html("cinemark", '<div class="sign-in-required">Members only</div>')
    assert res.rows is None
    assert "wall" in res.reason.lower()


def test_unrecognized_status_declines_rather_than_guessing():
    # Seats exist but carry an availability value the config doesn't know.
    html = (
        '<div class="seatRow">'
        '<button available="mysterystate" seattype="seat"></button>'
        '<button available="mysterystate" seattype="seat"></button>'
        "</div>"
    )
    res = parse_seat_html("cinemark", html)
    assert res.rows is None
    assert "availability" in res.reason.lower()


def test_no_seats_found_declines():
    res = parse_seat_html("cinemark", "<div id='map'><p>Nothing here</p></div>")
    assert res.rows is None
    assert "no seat elements" in res.reason.lower()


def test_amc_is_not_handled_by_the_dom_engine():
    """AMC uses the geometry strategy; its config intentionally has no selectors."""
    res = parse_seat_html("amc", '<div class="seatRow"><button available="True"></button></div>')
    assert res.rows is None


def test_unknown_chain_has_no_selectors():
    res = parse_seat_html("nickelodeon", "<div class='seat'></div>")
    assert res.rows is None
    assert "no seat-map selectors" in res.reason.lower()


def test_empty_html():
    res = parse_seat_html("cinemark", "")
    assert res.rows is None
