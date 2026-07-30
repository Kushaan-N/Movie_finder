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


def test_multiple_formats_use_or_matching(monkeypatch):
    clear_search_cache()
    from app.providers.base import ProviderShowtime

    async def fake_fetch(self, query):
        assert query.fmt == "Any"
        return [
            ProviderShowtime(theater_name="AMC Metreon 16", movie_title="M", format=fmt,
                             start_datetime=datetime(2026, 7, 27, 20, 0))
            for fmt in ("IMAX", "Dolby", "Standard")
        ]

    monkeypatch.setattr(search_mod.DemoProvider, "fetch", fake_fetch)
    req = SearchRequest(
        movie_title="M", location="94103", formats=["IMAX", "Dolby"],
        date_from="2026-07-27", date_to="2026-07-27",
    )
    res = asyncio.run(run_search(req, use_cache=False))
    assert {showtime.format for showtime in res.showtimes} == {"IMAX", "Dolby"}


def test_legacy_single_format_still_filters(monkeypatch):
    clear_search_cache()
    from app.providers.base import ProviderShowtime

    async def fake_fetch(self, query):
        return [
            ProviderShowtime(theater_name="AMC Metreon 16", movie_title="M", format=fmt,
                             start_datetime=datetime(2026, 7, 27, 20, 0))
            for fmt in ("IMAX", "Standard")
        ]

    monkeypatch.setattr(search_mod.DemoProvider, "fetch", fake_fetch)
    req = SearchRequest(
        movie_title="M", location="94103", format="IMAX",
        date_from="2026-07-27", date_to="2026-07-27",
    )
    res = asyncio.run(run_search(req, use_cache=False))
    assert {showtime.format for showtime in res.showtimes} == {"IMAX"}


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


def test_serpapi_path_returns_showtimes_end_to_end(monkeypatch, configure_provider):
    """The serpapi provider wins when configured, and its rows survive the pipeline.

    Guards the regression where the provider fanned out correctly but the parser
    read the wrong response shape and every search came back empty.
    """
    clear_search_cache()
    configure_provider(serpapi_key="test-key")

    today = datetime.now().date()

    async def fake_request(self, q, location):
        # One call per candidate theater, keyed on the theater name.
        assert "showtimes" not in q
        return {"showtimes": [{"day": "Today", "movies": [
            {"name": "The Odyssey", "link": "http://book", "showing": [
                {"type": "IMAX 70mm", "time": ["10:00pm"]},
            ]},
        ]}]}

    monkeypatch.setattr(search_mod.SerpApiProvider, "_request", fake_request)
    req = SearchRequest(
        movie_title="The Odyssey", location="94103", radius_miles=30,
        date_from=today.isoformat(), date_to=today.isoformat(),
    )
    res = asyncio.run(run_search(req, use_cache=False))

    assert res.meta.provider_used == "serpapi"
    assert res.meta.showtimes_returned > 0
    assert {s.format for s in res.showtimes} == {"70mm IMAX"}
    assert all(s.movie_title == "The Odyssey" for s in res.showtimes)


def test_serpapi_empty_result_note_names_the_theaters(monkeypatch, configure_provider):
    """An empty result should explain what was actually searched, not guess at ZIPs."""
    clear_search_cache()
    configure_provider(serpapi_key="test-key")

    async def fake_request(self, q, location):
        return {"showtimes": []}

    monkeypatch.setattr(search_mod.SerpApiProvider, "_request", fake_request)
    req = SearchRequest(movie_title="Nonexistent Film", location="94103", radius_miles=30)
    res = asyncio.run(run_search(req, use_cache=False))

    assert res.meta.showtimes_returned == 0
    note = " ".join(res.meta.notes)
    assert "Nonexistent Film" in note
    assert "AMC Metreon 16" in note  # names a theater it actually looked at


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


def test_local_filter_tweaks_do_not_refetch(monkeypatch, configure_provider):
    """Changing seat requirements must not cost another provider fetch.

    seats_together, min_row, the time window and the format filter are all applied
    locally, so they cannot change what the provider returns. Before the provider
    cache existed they still missed the response cache and re-fetched -- one SerpApi
    request per in-range theater, for a nudge of a number field.
    """
    clear_search_cache()
    configure_provider(serpapi_key="test-key")
    calls: list[str] = []

    async def fake_request(self, q, location):
        calls.append(q)
        return {"showtimes": [{"day": "Today", "movies": [
            {"name": "The Odyssey", "showing": [
                {"type": "IMAX", "time": ["7:00pm"]},
                {"type": "Standard", "time": ["9:00pm"]},
            ]}]}]}

    monkeypatch.setattr(search_mod.SerpApiProvider, "_request", fake_request)
    today = datetime.now().date().isoformat()
    base = dict(movie_title="The Odyssey", location="94103", radius_miles=30,
                date_from=today, date_to=today)

    first = asyncio.run(run_search(SearchRequest(**base, min_row=1, seats_together=2)))
    fetches = len(calls)
    assert fetches > 0 and first.meta.showtimes_returned > 0

    # Each of these changes only local filtering.
    for tweak in (
        dict(min_row=9, seats_together=2),
        dict(min_row=1, seats_together=8),
        dict(min_row=1, seats_together=2, formats=["IMAX"]),
        dict(min_row=1, seats_together=2,
             time_rule={"weekday_cutoff": "20:00", "weekends_unrestricted": False}),
    ):
        asyncio.run(run_search(SearchRequest(**base, **tweak)))
        assert len(calls) == fetches, f"re-fetched after {tweak}"


def test_a_different_movie_does_refetch(monkeypatch, configure_provider):
    """The provider cache must not serve one film's rows for another."""
    clear_search_cache()
    configure_provider(serpapi_key="test-key")
    calls: list[str] = []

    async def fake_request(self, q, location):
        calls.append(q)
        return {"showtimes": [{"day": "Today", "movies": [
            {"name": "The Odyssey", "showing": [{"type": "IMAX", "time": ["7:00pm"]}]}]}]}

    monkeypatch.setattr(search_mod.SerpApiProvider, "_request", fake_request)
    today = datetime.now().date().isoformat()
    asyncio.run(run_search(SearchRequest(movie_title="The Odyssey", location="94103",
                                         date_from=today, date_to=today, min_row=1)))
    after_first = len(calls)
    asyncio.run(run_search(SearchRequest(movie_title="Moana", location="94103",
                                         date_from=today, date_to=today, min_row=1)))
    assert len(calls) > after_first


def test_a_wider_radius_refetches(monkeypatch, configure_provider):
    """Radius changes which theaters are queried, so it must not be cached across."""
    clear_search_cache()
    configure_provider(serpapi_key="test-key")
    calls: list[str] = []

    async def fake_request(self, q, location):
        calls.append(q)
        return {"showtimes": [{"day": "Today", "movies": [
            {"name": "The Odyssey", "showing": [{"type": "IMAX", "time": ["7:00pm"]}]}]}]}

    monkeypatch.setattr(search_mod.SerpApiProvider, "_request", fake_request)
    today = datetime.now().date().isoformat()
    base = dict(movie_title="The Odyssey", location="94103", date_from=today,
                date_to=today, min_row=1)
    asyncio.run(run_search(SearchRequest(**base, radius_miles=5)))
    after_narrow = len(calls)
    asyncio.run(run_search(SearchRequest(**base, radius_miles=100)))
    assert len(calls) > after_narrow
