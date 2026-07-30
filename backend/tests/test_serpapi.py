"""SerpApi response parsing: relative dates, distance, format, range filter."""
from datetime import date, datetime, timedelta

from app.providers.base import ProviderQuery
from app.providers.serpapi_provider import (
    SerpApiProvider,
    _parse_distance,
    _resolve_date,
    _titles_match,
)
from app.services.theaters import Theater

TODAY = date(2026, 7, 26)


def _theater() -> Theater:
    return Theater(
        id="amc-metreon-16", name="AMC Metreon 16", chain="amc",
        address="135 4th St, San Francisco, CA", lat=37.7847, lng=-122.4033,
        formats=["IMAX", "Dolby", "Standard"], booking_base_url="http://amc.example",
    )


def test_resolve_relative_dates():
    assert _resolve_date("Today", "Jul 26", TODAY) == TODAY
    assert _resolve_date("Tomorrow", "", TODAY) == TODAY + timedelta(days=1)
    assert _resolve_date("Tue", "Jul 28", TODAY) == date(2026, 7, 28)
    assert _resolve_date("Sun", "8/2", TODAY) == date(2026, 8, 2)
    # A month already well in the past rolls to next year.
    assert _resolve_date("Sat", "Jan 3", TODAY) == date(2027, 1, 3)


def test_parse_distance_units():
    assert _parse_distance("2.3 mi") == 2.3
    assert _parse_distance("0.9 miles") == 0.9
    assert _parse_distance("4 km") == round(4 * 0.621371, 1)
    assert _parse_distance("") is None


def test_parse_full_response_filters_range_and_formats():
    now = datetime.now().date()
    far = now + timedelta(days=120)  # comfortably outside a 14-day window
    sample = {
        "showtimes": [
            {"day": "Today", "date": now.strftime("%b %d"), "theaters": [
                {"name": "AMC Metreon 16", "address": "SF", "link": "http://a", "distance": "0.9 mi",
                 "showing": [{"type": "IMAX", "time": ["7:15pm", "9:45 PM"]},
                             {"type": "Standard", "time": ["1:00pm"]}]},
            ]},
            {"day": "X", "date": far.strftime("%b %d"), "theaters": [  # outside 14-day window
                {"name": "Far Future", "showing": [{"type": "Standard", "time": ["5:00pm"]}]},
            ]},
        ]
    }
    q = ProviderQuery(movie_title="Dune", fmt="IMAX", location="SF",
                      date_from=now, date_to=now + timedelta(days=14))
    rows = SerpApiProvider()._parse(sample, q)

    # Only IMAX kept (format filter), Dec 25 dropped (range filter) -> 2 rows.
    assert len(rows) == 2
    assert {r.format for r in rows} == {"IMAX"}
    assert all(r.distance_miles == 0.9 for r in rows)
    assert rows[0].booking_url == "http://a"


def test_format_any_keeps_all():
    # "Today" resolves relative to the real current date inside _parse, so build
    # the query window from now() rather than a hardcoded date (avoids drift).
    now = datetime.now().date()
    sample = {"showtimes": [{"day": "Today", "date": now.strftime("%b %d"), "theaters": [
        {"name": "T", "showing": [{"type": "IMAX", "time": ["7:00pm"]},
                                   {"type": "Dolby Cinema", "time": ["8:00pm"]}]}]}]}
    q = ProviderQuery(movie_title="X", fmt="Any", location="SF",
                      date_from=now, date_to=now + timedelta(days=1))
    rows = SerpApiProvider()._parse(sample, q)
    assert {r.format for r in rows} == {"IMAX", "Dolby"}  # "Dolby Cinema" normalized


def test_titles_match_ignores_articles_and_subtitles():
    assert _titles_match("The Odyssey", "The Odyssey")
    assert _titles_match("Odyssey", "The Odyssey")  # user omits the article
    assert _titles_match("Dune", "Dune: Part Two")  # subset match
    assert not _titles_match("The Odyssey", "Moana")
    assert not _titles_match("", "The Odyssey")


def test_parse_theater_keyed_shape_selects_requested_movie():
    """A theater-name query returns day blocks keyed on `movies`, not `theaters`.

    This is the shape Google actually serves; the old parser read block["theaters"]
    and silently produced zero rows.
    """
    now = datetime.now().date()
    sample = {
        "showtimes": [
            {"day": "Today", "movies": [
                {"name": "The Odyssey", "link": "http://odyssey", "showing": [
                    {"type": "Standard", "time": ["6:30pm"]},
                    {"type": "IMAX 70mm", "time": ["10:00pm"]},
                ]},
                {"name": "Moana", "showing": [{"type": "Standard", "time": ["1:00pm"]}]},
            ]},
        ]
    }
    q = ProviderQuery(movie_title="The Odyssey", fmt="Any", location="94103",
                      date_from=now, date_to=now + timedelta(days=1))
    rows = SerpApiProvider()._parse_theater_keyed(sample, q, _theater())

    # Only the requested movie survives; Moana is dropped.
    assert len(rows) == 2
    assert {r.movie_title for r in rows} == {"The Odyssey"}
    assert {r.format for r in rows} == {"Standard", "70mm IMAX"}
    # Theater identity comes from theaters.json, not the SERP.
    assert all(r.theater_name == "AMC Metreon 16" and r.chain == "amc" for r in rows)
    assert rows[0].booking_url == "http://odyssey"


def test_parse_theater_keyed_respects_date_range_and_format():
    now = datetime.now().date()
    far = now + timedelta(days=120)
    sample = {
        "showtimes": [
            {"day": "Today", "movies": [
                {"name": "The Odyssey", "showing": [
                    {"type": "IMAX", "time": ["9:00pm"]},
                    {"type": "Standard", "time": ["6:00pm"]},
                ]},
            ]},
            {"day": "Sat", "date": far.strftime("%b %d"), "movies": [
                {"name": "The Odyssey", "showing": [{"type": "IMAX", "time": ["9:00pm"]}]},
            ]},
        ]
    }
    q = ProviderQuery(movie_title="The Odyssey", fmt="IMAX", location="94103",
                      date_from=now, date_to=now + timedelta(days=14))
    rows = SerpApiProvider()._parse_theater_keyed(sample, q, _theater())

    # Standard filtered by format, the far-future block filtered by range.
    assert len(rows) == 1
    assert rows[0].format == "IMAX"


def test_fetch_fans_out_one_request_per_theater(monkeypatch):
    """The theater list drives the requests, since movie queries return nothing."""
    import asyncio

    now = datetime.now().date()
    asked: list[str] = []

    async def fake_request(self, q, location):
        asked.append(q)
        return {"showtimes": [{"day": "Today", "movies": [
            {"name": "The Odyssey", "showing": [{"type": "Standard", "time": ["7:00pm"]}]}]}]}

    monkeypatch.setattr(SerpApiProvider, "_request", fake_request)

    t1 = _theater()
    t2 = Theater(id="amc-eastridge-15", name="AMC Eastridge 15", chain="amc",
                 address="San Jose, CA", lat=37.3, lng=-121.8,
                 formats=["Standard"], booking_base_url="http://amc2.example")
    q = ProviderQuery(movie_title="The Odyssey", fmt="Any", location="94103",
                      date_from=now, date_to=now + timedelta(days=1), theaters=[t1, t2])
    rows = asyncio.run(SerpApiProvider().fetch(q))

    assert asked == ["AMC Metreon 16", "AMC Eastridge 15"]
    assert {r.theater_name for r in rows} == {"AMC Metreon 16", "AMC Eastridge 15"}


def test_fetch_falls_back_to_movie_query_without_theaters(monkeypatch):
    import asyncio

    now = datetime.now().date()
    asked: list[str] = []

    async def fake_request(self, q, location):
        asked.append(q)
        return {"showtimes": [{"day": "Today", "theaters": [
            {"name": "Some Theater", "showing": [{"type": "Standard", "time": ["7:00pm"]}]}]}]}

    monkeypatch.setattr(SerpApiProvider, "_request", fake_request)
    q = ProviderQuery(movie_title="The Odyssey", fmt="Any", location="94103",
                      date_from=now, date_to=now + timedelta(days=1), theaters=[])
    rows = asyncio.run(SerpApiProvider().fetch(q))

    assert asked == ["The Odyssey showtimes"]
    assert [r.theater_name for r in rows] == ["Some Theater"]


def test_fetch_survives_one_failing_theater(monkeypatch):
    """One theater erroring must not lose the other theaters' showtimes."""
    import asyncio

    now = datetime.now().date()

    async def fake_request(self, q, location):
        if q == "AMC Metreon 16":
            raise RuntimeError("boom")
        return {"showtimes": [{"day": "Today", "movies": [
            {"name": "The Odyssey", "showing": [{"type": "Standard", "time": ["7:00pm"]}]}]}]}

    monkeypatch.setattr(SerpApiProvider, "_request", fake_request)
    t2 = Theater(id="amc-eastridge-15", name="AMC Eastridge 15", chain="amc",
                 address="San Jose, CA", lat=37.3, lng=-121.8,
                 formats=["Standard"], booking_base_url="http://amc2.example")
    q = ProviderQuery(movie_title="The Odyssey", fmt="Any", location="94103",
                      date_from=now, date_to=now + timedelta(days=1),
                      theaters=[_theater(), t2])
    rows = asyncio.run(SerpApiProvider().fetch(q))

    assert {r.theater_name for r in rows} == {"AMC Eastridge 15"}
