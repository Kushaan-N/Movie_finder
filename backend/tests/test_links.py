"""Showtime destinations.

Every URL shape asserted here was checked against the live site on 2026-07-29, so a
failure means either a regression or that a chain changed its scheme.
"""
from datetime import datetime

from app.links import build, chain_url, fandango_near_url, fandango_url

START = datetime(2026, 8, 1, 22, 30)


def test_amc_lands_on_the_theatre_and_date():
    url = chain_url("amc", "san-francisco/amc-metreon-16", START)
    assert url == (
        "https://www.amctheatres.com/movie-theatres/san-francisco/amc-metreon-16"
        "/showtimes?date=2026-08-01"
    )


def test_cinemark_lands_on_the_theatre_and_date():
    url = chain_url("cinemark", "ca-san-jose/cinemark-century-oakridge-20-xd-and-screenx", START)
    assert url.endswith("?showDate=2026-08-01")
    assert "cinemark.com/theatres/ca-san-jose/" in url


def test_regal_uses_its_own_date_format():
    """Regal takes MM-DD-YYYY, not ISO — verified live."""
    url = chain_url("regal", "regal-hacienda-crossings-0347", START)
    assert url.endswith("?date=08-01-2026")


def test_no_chain_slug_means_no_chain_link():
    assert chain_url("amc", "", START) is None


def test_unknown_chain_has_no_link():
    assert chain_url("nickelodeon", "some/slug", START) is None


def test_fandango_searches_the_movie_alone():
    """Adding the theatre name makes Fandango return no results at all."""
    url = fandango_url("The Odyssey", "AMC Metreon 16")
    assert url == "https://www.fandango.com/search?q=The+Odyssey"
    assert "Metreon" not in url


def test_fandango_near_only_for_a_zip():
    assert fandango_near_url("94103") == "https://www.fandango.com/94103_movietimes"
    assert fandango_near_url("94103-1234") == "https://www.fandango.com/94103_movietimes"
    assert fandango_near_url("San Jose, California") is None
    assert fandango_near_url("") is None


def test_build_prefers_the_chain_page():
    links = build(
        chain="amc", chain_slug="san-francisco/amc-metreon-16",
        movie_title="The Odyssey", theater_name="AMC Metreon 16", start=START,
        provider_url="https://www.google.com/search?q=x",
    )
    assert links["best"] == links["chain"]
    assert links["chain_label"] == "AMC"
    assert links["fandango"].startswith("https://www.fandango.com/search")
    assert links["search"].startswith("https://www.google.com/")


def test_build_falls_back_to_fandango_without_a_slug():
    """A theatre we have no slug for still gets somewhere real to go."""
    links = build(
        chain="amc", chain_slug="", movie_title="The Odyssey",
        theater_name="Some Indie Cinema", start=START, provider_url=None,
    )
    assert links["chain"] is None
    assert links["best"] == links["fandango"]
    assert links["best"].startswith("https://www.fandango.com/")


def test_best_is_never_a_google_search_when_a_chain_page_exists():
    """The provider's link is a search results page; it must not be the default."""
    links = build(
        chain="cinemark", chain_slug="ca-san-jose/x", movie_title="M",
        theater_name="T", start=START, provider_url="https://www.google.com/search?q=x",
    )
    assert "google.com" not in links["best"]
