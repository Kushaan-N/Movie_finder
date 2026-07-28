"""SerpApi response parsing: relative dates, distance, format, range filter."""
from datetime import date, datetime, timedelta

from app.providers.base import ProviderQuery
from app.providers.serpapi_provider import SerpApiProvider, _parse_distance, _resolve_date

TODAY = date(2026, 7, 26)


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
