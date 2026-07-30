"""The chain-agnostic extractor, run by a real browser.

Geometry and paint can only be evaluated by a real layout engine, so these are
Playwright-gated and drive the actual ``seat_extract.js`` the bookmarklet ships.

Fixtures reproduce the two markup families verified live on 2026-07-29:
Cinemark's explicit ``available`` attributes and AMC's attribute-free SVG whose
availability lives only in gradient stop-colors.
"""
import importlib.util
import pathlib

import pytest

from tests._fixture_server import serve_fixtures

_HAS_PW = importlib.util.find_spec("playwright") is not None

SRC = (
    pathlib.Path(__file__).parents[1] / "app" / "scrape" / "seat_extract.js"
).read_text(encoding="utf-8").strip()


def _extract(url: str, options: str = "{}") -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="load")
            page.wait_for_timeout(400)
            return page.evaluate(f"({SRC})({options})")
        finally:
            browser.close()


def _render(rows) -> list[str]:
    """Render the extractor's grid as strings: O free, . taken, _ gap."""
    return [
        "".join("_" if c["gap"] else ("O" if c["available"] else ".") for c in row)
        for row in rows
    ]


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_reads_cinemark_style_attributes():
    """Explicit availability attributes are the most trustworthy signal."""
    with serve_fixtures() as base:
        res = _extract(f"{base}/TicketSeatMap/", "{minSeats:10, minRows:3}")

    assert res["ok"] is True
    assert res["strategy"] == "attr"
    # Row A: two taken, an aisle, one free, one taken. Row C: five free, then a
    # wheelchair and companion position, which are not general seating.
    assert _render(res["rows"]) == [".._O.", "O.O", "OOOOO__"]
    assert res["stats"]["available_found"] == 8


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_reads_amc_style_svg_with_no_attributes_or_text():
    """No seat attributes, no text, availability only in gradient stop-colors."""
    with serve_fixtures() as base:
        res = _extract(f"{base}/showtimes/1234/seats")

    assert res["ok"] is True
    assert res["strategy"] == "paint"
    # 3 rows of 10; only the last has a run of 4 free at indexes 2..5.
    assert _render(res["rows"]) == ["..........", "..........", "..OOOO...."]
    assert res["stats"]["available_found"] == 4


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_page_chrome_never_becomes_a_row():
    """A phantom row would shift every physical row and corrupt min_row."""
    with serve_fixtures() as base:
        res = _extract(f"{base}/showtimes/1234/seats")

    assert res["stats"]["rows_found"] == 3      # the 2-element chrome row is dropped
    assert res["stats"]["seats_found"] == 30    # decoration excluded too


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_declines_on_a_page_with_no_seat_map():
    """It must refuse rather than invent a grid."""
    with serve_fixtures() as base:
        res = _extract(f"{base}/robots.txt")

    assert res["ok"] is False
    assert res["rows"] == []
    assert "no seat map" in (res["reason"] or "").lower()


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_reports_which_strategy_and_colour_source_were_used():
    """Provenance is shown in the UI, so it has to be populated."""
    with serve_fixtures() as base:
        res = _extract(f"{base}/showtimes/1234/seats")

    assert res["strategy"] == "paint"
    # The fixture has no legend, so the painted/unpainted split must carry it:
    # AMC draws a taken seat as a gradient whose stops are all transparent.
    assert res["stats"]["colour_source"] == "painted-vs-unpainted"
    assert res["url"].endswith("/seats")


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_reads_a_map_whose_state_is_only_enabled_or_disabled():
    """Free seats are live controls; taken ones are disabled or aria-disabled.

    This is the strategy that has to carry chains whose seat markup could not be
    inspected in advance, so it is exercised on its own terms rather than assumed.
    """
    with serve_fixtures() as base:
        res = _extract(f"{base}/interactive-seats", "{minSeats:20, minRows:3}")

    assert res["ok"] is True
    assert res["strategy"] == "interactive"
    # 4 rows x 12 with an aisle after seat 6. Row 3 has six free seats, but five of
    # them sit before the aisle and one after.
    assert _render(res["rows"]) == [
        "......_......",
        "O....._.....O",
        ".OOOOO_O.....",
        "......_......",
    ]
    assert res["stats"]["seats_found"] == 48


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_aisle_splits_a_run_in_the_interactive_map():
    """Row 3 has six free seats straddling an aisle; the run must not span it."""
    with serve_fixtures() as base:
        res = _extract(f"{base}/interactive-seats", "{minSeats:20, minRows:3}")

    row3 = res["rows"][2]
    runs, cur = [], 0
    for cell in row3:
        if cell["available"] and not cell["gap"]:
            cur += 1
        else:
            runs.append(cur)
            cur = 0
    runs.append(cur)
    free = sum(1 for c in row3 if c["available"] and not c["gap"])
    assert free == 6            # six free seats in the row...
    assert max(runs) == 5       # ...but never a 6-run, because an aisle divides them
