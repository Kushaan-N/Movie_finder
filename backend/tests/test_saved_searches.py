"""Saved searches and the diff-on-rerun.

The diff is a headline feature and had no coverage at all: nothing pinned that a
first run doesn't flag everything as new, that a genuinely new showtime IS flagged,
or that the snapshot advances so the same showtime isn't reported new forever.

The provider is stubbed so the diff is tested against controlled showtimes rather
than whatever is playing today.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.base import ProviderShowtime
from app.services import search as search_mod
from app.services.search import clear_search_cache

client = TestClient(app)

CONFIG = {
    "movie_title": "The Odyssey", "formats": ["Any"], "location": "94103",
    "radius_miles": 25, "date_from": "2026-08-01", "date_to": "2026-08-01",
    "time_rule": {"weekday_cutoff": "00:00", "weekends_unrestricted": True},
    "seats_together": 4, "min_row": 1,
}


def _showings(times):
    return [
        ProviderShowtime(
            theater_name="AMC Metreon 16", movie_title="The Odyssey", format="IMAX",
            start_datetime=datetime(2026, 8, 1, h, m),
        )
        for h, m in times
    ]


@pytest.fixture
def provider(monkeypatch):
    """Drive the demo provider's output so the diff has a known input."""
    state = {"times": [(19, 0), (22, 0)]}

    async def fake_fetch(self, query):
        return _showings(state["times"])

    monkeypatch.setattr(search_mod.DemoProvider, "fetch", fake_fetch)
    clear_search_cache()
    return state


@pytest.fixture
def saved(provider):
    """A saved search, cleaned up afterwards."""
    r = client.post("/api/saved-searches", json={"name": "diff-test", "config": CONFIG})
    assert r.status_code == 200
    sid = r.json()["id"]
    yield sid
    client.delete(f"/api/saved-searches/{sid}")


def _run(sid):
    r = client.post(f"/api/saved-searches/{sid}/run")
    assert r.status_code == 200, r.text
    return r.json()


def test_first_run_flags_nothing_as_new():
    """With no previous snapshot, "new since last run" is meaningless."""
    # Uses its own saved row so ordering with other tests can't matter.
    r = client.post("/api/saved-searches", json={"name": "first-run", "config": CONFIG})
    sid = r.json()["id"]
    try:
        body = _run(sid)
        assert body["new_count"] == 0
        assert not any(st["is_new"] for st in body["showtimes"])
    finally:
        client.delete(f"/api/saved-searches/{sid}")


def test_a_genuinely_new_showtime_is_flagged(provider, saved):
    first = _run(saved)
    assert first["meta"]["showtimes_returned"] == 2

    provider["times"] = [(19, 0), (22, 0), (23, 30)]   # a showtime appears
    clear_search_cache()
    second = _run(saved)

    assert second["new_count"] == 1
    flagged = [st for st in second["showtimes"] if st["is_new"]]
    assert len(flagged) == 1
    assert flagged[0]["start_time_label"] == "11:30 PM"


def test_the_snapshot_advances_so_new_is_not_sticky(provider, saved):
    _run(saved)
    provider["times"] = [(19, 0), (22, 0), (23, 30)]
    clear_search_cache()
    assert _run(saved)["new_count"] == 1

    # Same set again: the once-new showtime is now part of the baseline.
    clear_search_cache()
    third = _run(saved)
    assert third["new_count"] == 0
    assert not any(st["is_new"] for st in third["showtimes"])


def test_a_disappearing_showtime_is_not_counted_as_new(provider, saved):
    provider["times"] = [(19, 0), (22, 0), (23, 30)]
    clear_search_cache()
    _run(saved)

    provider["times"] = [(19, 0)]                       # two showtimes vanish
    clear_search_cache()
    body = _run(saved)
    assert body["meta"]["showtimes_returned"] == 1
    assert body["new_count"] == 0


def test_rerun_bypasses_the_search_cache(provider, saved):
    """A re-run must re-fetch, or the diff would compare a result to itself."""
    _run(saved)
    provider["times"] = [(19, 0), (22, 0), (23, 30)]
    # Deliberately NOT clearing the cache: the endpoint has to bypass it.
    assert _run(saved)["meta"]["showtimes_returned"] == 3


def test_saving_the_same_name_updates_in_place():
    a = client.post("/api/saved-searches", json={"name": "dupe", "config": CONFIG}).json()
    changed = {**CONFIG, "min_row": 7}
    b = client.post("/api/saved-searches", json={"name": "dupe", "config": changed}).json()
    try:
        assert a["id"] == b["id"]
        assert b["config"]["min_row"] == 7
        names = [s["name"] for s in client.get("/api/saved-searches").json()]
        assert names.count("dupe") == 1
    finally:
        client.delete(f"/api/saved-searches/{a['id']}")


def test_running_a_missing_saved_search_is_a_404():
    assert client.post("/api/saved-searches/999999/run").status_code == 404
    assert client.delete("/api/saved-searches/999999").status_code == 404


def test_saved_results_carry_showtime_links(provider, saved):
    """The diff view shows the same cards, so they need their destinations too."""
    body = _run(saved)
    links = body["showtimes"][0]["links"]
    assert links["best"]
    assert "google.com/search" not in links["best"]
