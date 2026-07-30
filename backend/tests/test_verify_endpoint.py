"""/api/verify-seats endpoint.

The gating branches run without a browser; the real-verification branches are
Playwright-gated and drive the whole pipeline against a local fixture server
(listing -> seat-URL resolution -> render -> parse -> seat check).
"""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.scrape import resolver
from tests._fixture_server import serve_fixtures

_HAS_PW = importlib.util.find_spec("playwright") is not None

_START = "2026-08-01T23:00:00"  # the 11:00pm entry in both listing fixtures


def test_verify_disabled_returns_available_false(monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_seat_verification", False, raising=False)
    client = TestClient(app)
    r = client.post(
        "/api/verify-seats",
        json={"chain": "cinemark", "theater_id": "cinemark-century-20-oakridge",
              "start_datetime": _START},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["seat_check"]["status"] == "check_manually"


def test_disabled_chain_reports_policy_rather_than_pretending(monkeypatch):
    """Cinemark's seat map is robots-disallowed; the endpoint must say so."""
    monkeypatch.setattr(get_settings(), "enable_seat_verification", True, raising=False)
    client = TestClient(app)
    r = client.post(
        "/api/verify-seats",
        json={"chain": "cinemark", "theater_id": "cinemark-century-20-oakridge",
              "start_datetime": _START},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["seat_check"]["status"] == "check_manually"
    assert "robots.txt" in (body["reason"] or "").lower()


def _point_resolver_at(monkeypatch, base: str, path: str):
    """Swap only the listing host, so resolution itself is still exercised."""
    monkeypatch.setattr(resolver, "listing_url", lambda chain, slug, day: f"{base}{path}")


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_cinemark_dom_strategy_end_to_end(monkeypatch):
    """Cinemark is policy-disabled in production; re-enabled here so the `dom`
    strategy stays proven for the day that policy changes."""
    from app.scrape.seatmap import _load_selectors
    monkeypatch.delitem(_load_selectors()["chains"]["cinemark"], "disabled", raising=False)
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)
    client = TestClient(app)
    with serve_fixtures() as base:
        _point_resolver_at(monkeypatch, base, "/cinemark-listing")
        r = client.post(
            "/api/verify-seats",
            json={"chain": "cinemark", "theater_id": "cinemark-century-20-oakridge",
                  "start_datetime": _START, "seats_together": 5, "min_row": 1},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["seat_check"]["status"] == "match"
    # Row C is DOM index 2 -> physical row 3, holding the run of 5.
    assert body["seat_check"]["best_block_row"]["physical_row"] == 3
    assert len(body["grid"]) == 3
    assert body["grid"][2]["seats_available"][:5] == [True, True, True, True, True]


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_amc_geometry_strategy_end_to_end(monkeypatch):
    """Drives the real in-page geometry+gradient extraction in a real browser."""
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)
    client = TestClient(app)
    with serve_fixtures() as base:
        _point_resolver_at(monkeypatch, base, "/amc-listing")
        r = client.post(
            "/api/verify-seats",
            json={"chain": "amc", "theater_id": "amc-metreon-16",
                  "start_datetime": _START, "seats_together": 4, "min_row": 1},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["seat_check"]["status"] == "match"
    # Fixture: 3 rows x 10; only the last row has a run of 4 open seats.
    assert body["seat_check"]["best_block_size"] == 4
    assert body["seat_check"]["best_block_row"]["physical_row"] == 3
    assert len(body["grid"]) == 3
    assert body["stats"]["seats_found"] == 30  # decorative non-seat glyph excluded


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_amc_geometry_honors_min_row_end_to_end(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)
    client = TestClient(app)
    with serve_fixtures() as base:
        _point_resolver_at(monkeypatch, base, "/amc-listing")
        r = client.post(
            "/api/verify-seats",
            json={"chain": "amc", "theater_id": "amc-metreon-16",
                  "start_datetime": _START, "seats_together": 4, "min_row": 4},
        )
    # The only qualifying block is at physical row 3, so row 4+ must not match.
    assert r.json()["seat_check"]["status"] == "no_match"
