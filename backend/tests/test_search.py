"""Search orchestration: time-window rule, cache behavior, demo integration."""
import asyncio
from datetime import datetime, time

from app.schemas import SearchRequest
from app.services import search as search_mod
from app.services.search import _parse_cutoff, _passes_time_rule, clear_search_cache, run_search

# 2026-07-27 is a Monday; 2026-07-25 is a Saturday.
MON_5PM = datetime(2026, 7, 27, 17, 0)
MON_7PM = datetime(2026, 7, 27, 19, 0)
SAT_2PM = datetime(2026, 7, 25, 14, 0)
CUTOFF = time(18, 30)


def test_parse_cutoff_handles_bad_input():
    assert _parse_cutoff("18:30") == time(18, 30)
    assert _parse_cutoff("garbage") == time(18, 30)  # safe default


def test_weekday_cutoff_filters_early_showings():
    assert _passes_time_rule(MON_5PM, CUTOFF, weekends_unrestricted=True) is False
    assert _passes_time_rule(MON_7PM, CUTOFF, weekends_unrestricted=True) is True


def test_weekend_unrestricted_toggle():
    # Saturday afternoon: allowed when unrestricted, blocked when restricted.
    assert _passes_time_rule(SAT_2PM, CUTOFF, weekends_unrestricted=True) is True
    assert _passes_time_rule(SAT_2PM, CUTOFF, weekends_unrestricted=False) is False


def test_demo_search_runs_end_to_end_without_keys():
    clear_search_cache()
    req = SearchRequest(movie_title="Dune", location="94103", format="IMAX")
    res = asyncio.run(run_search(req))
    assert res.meta.provider_used == "demo"
    assert res.meta.showtimes_returned > 0
    # Every demo weekday showing respects the default 6:30pm cutoff.
    for st in res.showtimes:
        if st.start_datetime.weekday() < 5:
            assert st.start_datetime.time() >= time(18, 30)


def test_radius_filter_uses_provider_distance(monkeypatch):
    clear_search_cache()
    from app.providers.base import ProviderShowtime

    async def fake_fetch(self, query):
        return [
            ProviderShowtime(theater_name="Near", movie_title="M", format="IMAX",
                             start_datetime=datetime(2026, 7, 27, 20, 0), distance_miles=3.0),
            ProviderShowtime(theater_name="Far", movie_title="M", format="IMAX",
                             start_datetime=datetime(2026, 7, 27, 20, 0), distance_miles=50.0),
        ]

    # Force the demo provider to be the one used and feed it our two rows.
    monkeypatch.setattr(search_mod.DemoProvider, "fetch", fake_fetch)
    req = SearchRequest(movie_title="M", location="94103", radius_miles=10)
    res = asyncio.run(run_search(req, use_cache=False))
    names = {s.theater_name for s in res.showtimes}
    assert names == {"Near"}  # Far (50mi) filtered out by the 10mi radius


def test_cache_hit_and_bypass():
    clear_search_cache()
    req = SearchRequest(movie_title="Cached Movie", location="94103")
    asyncio.run(run_search(req, use_cache=True))
    assert len(search_mod._search_cache) == 1  # populated
    # A cached read returns an equal (deep-copied) response.
    again = asyncio.run(run_search(req, use_cache=True))
    assert again.meta.showtimes_returned > 0
    # Bypass does not require the cache.
    clear_search_cache()
    asyncio.run(run_search(req, use_cache=False))
    assert len(search_mod._search_cache) == 0
