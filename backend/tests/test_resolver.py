"""Showtime -> chain seat-URL resolution.

Fixtures mirror markup verified live on 2026-07-29. This exists because
SerpApi's per-showing link is a google.com search URL, so the seat page has to be
found on the chain's own listing.
"""
import os
from datetime import datetime

from app.scrape import resolver

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_supports_only_chains_with_reachable_seat_pages():
    assert resolver.supports("cinemark")
    assert resolver.supports("amc")
    # Regal's seat page is CAPTCHA-gated, so there is nothing to resolve to.
    assert not resolver.supports("regal")


def test_cinemark_candidates_extracts_seatmap_links():
    cands = resolver.cinemark_candidates(_fixture("cinemark_listing.html"))
    assert len(cands) == 3
    labels = [c[0] for c in cands]
    assert "11:00pm" in labels
    assert all(u.startswith("https://www.cinemark.com/TicketSeatMap/") for _, u in cands)


def test_amc_candidates_appends_seats_to_showtime_links():
    cands = resolver.amc_candidates(_fixture("amc_listing.html"))
    assert len(cands) == 3
    urls = [u for _, u in cands]
    assert "https://www.amctheatres.com/showtimes/144251397/seats" in urls
    assert all(u.endswith("/seats") for u in urls)


def test_resolve_picks_the_matching_start_time():
    html = _fixture("cinemark_listing.html")
    url = resolver.resolve_from_listing("cinemark", html, datetime(2026, 8, 1, 23, 0))
    assert url and "ShowtimeId=770106" in url

    url = resolver.resolve_from_listing("cinemark", html, datetime(2026, 8, 1, 19, 30))
    assert url and "ShowtimeId=770101" in url


def test_resolve_tolerates_small_clock_drift():
    """Google's reported time and the chain's listing can differ by a minute."""
    html = _fixture("cinemark_listing.html")
    url = resolver.resolve_from_listing("cinemark", html, datetime(2026, 8, 1, 19, 32))
    assert url and "ShowtimeId=770101" in url  # nearest to 7:30pm


def test_resolve_declines_when_no_time_is_close_enough():
    html = _fixture("cinemark_listing.html")
    assert resolver.resolve_from_listing("cinemark", html, datetime(2026, 8, 1, 14, 0)) is None


def test_resolve_prefers_the_closest_of_several_nearby_times():
    html = _fixture("cinemark_listing.html")  # has both 7:30pm and 7:45pm
    url = resolver.resolve_from_listing("cinemark", html, datetime(2026, 8, 1, 19, 44))
    assert url and "ShowtimeId=770102" in url  # 7:45pm, not 7:30pm


def test_listing_urls_carry_the_requested_date():
    day = datetime(2026, 8, 1, 23, 0)
    cm = resolver.listing_url("cinemark", "ca-san-jose/century-20-oakridge-and-xd", day)
    assert cm and cm.endswith("showDate=2026-08-01")
    amc = resolver.listing_url("amc", "san-francisco/amc-metreon-16", day)
    assert amc and amc.endswith("/showtimes?date=2026-08-01")
    assert resolver.listing_url("regal", "whatever", day) is None
