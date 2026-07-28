"""/api/verify-seats endpoint. The 'available' branches run without a browser;
the real-verification branch is exercised in the Playwright-gated test below."""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from tests._fixture_server import serve_fixtures

_HAS_PW = importlib.util.find_spec("playwright") is not None


def test_verify_disabled_returns_available_false(monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_seat_verification", False, raising=False)
    client = TestClient(app)
    r = client.post("/api/verify-seats", json={"chain": "amc", "booking_url": "http://x/amc"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["seat_check"]["status"] == "check_manually"


@pytest.mark.skipif(not _HAS_PW, reason="Playwright not installed")
def test_verify_endpoint_real_browser(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "enable_seat_verification", True, raising=False)
    monkeypatch.setattr(s, "scrape_rate_limit_per_sec", 50, raising=False)
    client = TestClient(app)
    with serve_fixtures() as base:
        r = client.post(
            "/api/verify-seats",
            json={"chain": "cinemark", "booking_url": f"{base}/cinemark", "seats_together": 5, "min_row": 5},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["seat_check"]["status"] == "match"
    assert body["seat_check"]["best_block_row"]["physical_row"] == 7
    # Grid preview is returned for the UI.
    assert len(body["grid"]) == 7
    assert body["grid"][-1]["seats_available"] == [True, True, True, True, True]
