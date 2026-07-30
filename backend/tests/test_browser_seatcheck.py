"""Browser-assisted seat checking: the grid endpoint and the bookmarklet.

This is the only route that reaches every chain, so it needs to be as trustworthy
as the automated path. The grids below are the real payload shape the bookmarklet
produced from AMC's live seat map on 2026-07-29.
"""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.scrape.bookmarklet import FRAGMENT_KEY, build_href, build_js, extractor_source

client = TestClient(app)

# Read from AMC Metreon 16, Sat Aug 1 10:30 PM: 45 free, all in the front rows.
AMC_GRID = [
    "OOOOOOOOOOOOOOOOOOOO",
    "OOOOOOOO....OOOOOOOOOO",
    "OO.O......OOO.....",
    "....................O.",
    "......................",
    "......................",
    "......................._.",
    "......................._.",
    "........._.......",
]


def _post(rows, seats_together=4, min_row=1, **kw):
    body = {"rows": rows, "chain": kw.pop("chain", "amc"),
            "theater_id": kw.pop("theater_id", "amc-metreon-16"),
            "seats_together": seats_together, "min_row": min_row, **kw}
    r = client.post("/api/verify-seats/from-grid", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- the grid endpoint ------------------------------------------------------- #

def test_real_amc_grid_counts_match_the_page():
    d = _post(AMC_GRID)
    assert d["stats"]["rows_found"] == 9
    assert d["stats"]["available_found"] == 45   # verified against a screenshot
    assert d["stats"]["source"] == "browser"


def test_front_row_run_is_found():
    d = _post(AMC_GRID, seats_together=4, min_row=1)
    sc = d["seat_check"]
    assert sc["status"] == "match"
    assert sc["best_block_size"] == 20          # row 1 is wide open
    assert sc["best_block_row"]["physical_row"] == 1


def test_min_row_actually_filters():
    """The whole point of the feature: rows nearer the screen must be excluded."""
    assert _post(AMC_GRID, seats_together=4, min_row=3)["seat_check"]["status"] == "no_match"
    # Row 5 back is fully taken on this map, so nothing qualifies there either.
    assert _post(AMC_GRID, seats_together=4, min_row=5)["seat_check"]["status"] == "no_match"


def test_row_two_split_by_taken_seats_limits_the_run():
    # "OOOOOOOO....OOOOOOOOOO" -> longest run behind row 1 is 10.
    sc = _post(AMC_GRID, seats_together=4, min_row=2)["seat_check"]
    assert sc["status"] == "match"
    assert sc["best_block_size"] == 10


def test_gap_breaks_contiguity():
    """An aisle must not join two blocks into one."""
    sc = _post(["OOOO_OOOO", "OOOO_OOOO", "OOOO_OOOO"], seats_together=8, min_row=1)["seat_check"]
    assert sc["status"] == "no_match"
    assert sc["best_block_size"] == 4


def test_physical_rows_are_screen_first_and_sequential():
    d = _post(["OOOO", "OOOO", "OOOO"], seats_together=2, min_row=1)
    assert [row["physical_row"] for row in d["grid"]] == [1, 2, 3]


def test_grid_is_echoed_for_eyeballing():
    d = _post(AMC_GRID)
    assert len(d["grid"]) == 9
    assert d["grid"][0]["seats_available"] == [True] * 20
    assert all(a is False for a in d["grid"][4]["seats_available"])


def test_strategy_and_source_are_echoed_back():
    d = _post(AMC_GRID, strategy="paint", source_url="https://example.test/seats")
    assert d["stats"]["strategy"] == "paint"
    assert d["stats"]["source_url"] == "https://example.test/seats"


def test_empty_payload_is_rejected():
    assert client.post("/api/verify-seats/from-grid", json={"rows": []}).status_code == 422
    assert client.post("/api/verify-seats/from-grid", json={"rows": [""]}).status_code == 400


def test_works_for_a_chain_the_server_cannot_scrape():
    """Regal and Cinemark are unreachable server-side; this path doesn't care."""
    for chain in ("regal", "cinemark"):
        d = _post(["OOOO", "OOOO", "OOOO"], seats_together=4, min_row=1,
                  chain=chain, theater_id=f"{chain}-x")
        assert d["seat_check"]["status"] == "match"


# --- the bookmarklet -------------------------------------------------------- #

def test_bookmarklet_embeds_the_extractor_and_hands_off_by_fragment():
    js = build_js("http://localhost:5173")
    # Inlined, because loading it from localhost is blocked by Private Network Access.
    assert "getBoundingClientRect" in js
    assert f"#{FRAGMENT_KEY}=" in js
    # It must never try to reach the server from the chain's page.
    assert "fetch(" not in js
    assert "XMLHttpRequest" not in js


def test_bookmarklet_href_is_a_javascript_url():
    href = build_href("http://localhost:5173")
    assert href.startswith("javascript:")
    assert "%20" in href or "%28" in href  # percent-encoded, safe in an href


def test_extractor_stays_a_bare_expression():
    """Consumers invoke it as (<src>)(opts); a trailing semicolon breaks that."""
    raw = extractor_source(False).strip()
    assert raw.endswith("})")
    assert not raw.endswith(");")
    # With the header comment stripped it is a bare function expression.
    assert extractor_source(True).strip().startswith("(function")


def test_minifying_preserves_the_regex_literals():
    """Comment stripping must not eat the url(#id) or rgb() patterns."""
    mini = extractor_source(True)
    assert "url\\(#" in mini
    assert "rgba?\\(" in mini
    assert "/*" not in mini


def test_bookmarklet_endpoint_reports_how_to_use_it():
    d = client.get("/api/seat-bookmarklet", params={"app_url": "http://x.test"}).json()
    assert d["href"].startswith("javascript:")
    assert d["fragment_key"] == FRAGMENT_KEY
    assert d["bytes"] > 1000
    assert any("bookmarks bar" in step for step in d["how_to"])


def test_setup_page_serves_a_draggable_link():
    r = client.get("/api/seat-bookmarklet/setup", params={"app_url": "http://x.test"})
    assert r.status_code == 200
    assert "javascript:" in r.text
    assert "Check seats" in r.text


def test_handoff_payload_round_trips():
    """What the bookmarklet builds must be what the endpoint accepts."""
    payload = {"rows": AMC_GRID, "strategy": "paint", "chain": "amc",
               "theater_id": "amc-metreon-16", "source_url": "https://x.test"}
    # The bookmarklet JSON-encodes exactly this shape into the fragment.
    revived = json.loads(json.dumps(payload))
    d = _post(revived["rows"], strategy=revived["strategy"],
              source_url=revived["source_url"])
    assert d["stats"]["available_found"] == 45


# --- config actually reaching the extractor ---------------------------------- #

def test_config_keys_are_translated_to_extractor_options():
    """scrape_selectors.json is snake_case; the extractor's options are camelCase.

    They used to be passed through untranslated, so no tuning value reached the
    extractor and it silently used its own defaults -- editing the config did
    nothing, which is worse than the config not existing.
    """
    from app.scrape.extract import extractor_options

    opts = extractor_options({
        "seat_min_px": 5, "seat_max_px": 90, "row_tolerance_px": 20,
        "min_seats_expected": 30, "min_row_width": 6, "aisle_gap_factor": 2.5,
        "strategy": "geometry", "login_wall_text": ["x"],   # not extractor options
    })
    assert opts == {
        "seatMinPx": 5, "seatMaxPx": 90, "rowTolerancePx": 20,
        "minSeats": 30, "minRowWidth": 6, "aisleGapFactor": 2.5,
    }


def test_call_expression_embeds_the_translated_options():
    from app.scrape.extract import call_expression

    expr = call_expression({"min_seats_expected": 42})
    assert '"minSeats": 42' in expr
    assert "min_seats_expected" not in expr
    # A parenthesised expression immediately invoked with the options.
    assert expr.startswith("(") and expr.rstrip().endswith('({"minSeats": 42})')


def test_the_live_amc_config_translates_to_something_usable():
    """A guard against renaming a config key and silently losing it."""
    from app.scrape.extract import extractor_options
    from app.scrape.seatmap import _chain_cfg

    opts = extractor_options(_chain_cfg("amc") or {})
    assert opts.get("minSeats"), "AMC's min_seats_expected no longer reaches the extractor"
    assert opts.get("seatMinPx") and opts.get("seatMaxPx")
