"""Occupancy read from a chain's showtimes listing.

The markup shapes here mirror what the live listings served on 2026-07-30: AMC
writes the state into the showtime link's text ("6:00pm Almost Full"), Regal
marks sold-out showings with a disabled button and an aria-label.

The interesting risk is over-claiming. Occupancy is a cheap signal and it must
never be read as an answer to "are there N seats together" — these tests pin the
distinction as much as the parsing.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.scrape import availability


def amc_html(entries):
    """An AMC listing: showtimes wrapped in a film container with a /movies/ link."""
    rows = "".join(
        f'<a href="/showtimes/{i}00">{label}</a>' for i, label in enumerate(entries, 1)
    )
    return f"""
    <div><a href="/movies/the-odyssey-77425">The Odyssey</a>
      <div>{rows}</div>
    </div>
    <div><a href="/movies/spider-man-brand-new-day-ho000212">Spider-Man</a>
      <div><a href="/showtimes/999">6:00pm Sold Out</a></div>
    </div>
    """


def regal_html(entries):
    buttons = "".join(
        f'<button {"disabled" if sold else ""} aria-label="{label} showtime'
        f'{", sold out" if sold else ""}">{label}</button>'
        for label, sold in entries
    )
    return f"""
    <div><a href="/movies/the-odyssey-hodyssey">The Odyssey</a>
      <div>{buttons}</div>
    </div>
    """


class TestNormalizeTime:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("6:00pm", "18:00"),
            ("6:00 PM", "18:00"),
            ("6:00 p.m.", "18:00"),
            ("10:00am UP TO 15% OFF, Almost Full", "10:00"),
            ("12:01am", "00:01"),
            ("12:30pm", "12:30"),
        ],
    )
    def test_same_showing_keys_identically_however_written(self, label, expected):
        # This is what maps a parsed row back to the showtime it belongs to, so a
        # formatting difference must not become a missed match.
        assert availability.normalize_time(label) == expected

    def test_text_without_a_time_is_none(self):
        assert availability.normalize_time("Almost Full") is None
        assert availability.normalize_time("") is None


class TestAmc:
    def test_reads_the_three_states(self):
        got = availability.from_amc(
            amc_html(["1:00pm Sold Out", "4:00pm Almost Full", "7:00pm"]), "The Odyssey"
        )
        assert got == {"13:00": "sold_out", "16:00": "almost_full", "19:00": "seats_available"}

    def test_discount_text_is_not_an_occupancy_claim(self):
        # "UP TO 15% OFF" sits in the same label; only the occupancy words count.
        got = availability.from_amc(amc_html(["10:00am UP TO 15% OFF"]), "The Odyssey")
        assert got == {"10:00": "seats_available"}

    def test_only_the_requested_film(self):
        # A listing carries every film that day. The Spider-Man 6:00pm entry above
        # is sold out; attributing it to The Odyssey would report the wrong film's
        # state -- the exact confusion that once resolved one film to another's map.
        got = availability.from_amc(amc_html(["6:00pm Almost Full"]), "The Odyssey")
        assert got == {"18:00": "almost_full"}


class TestRegal:
    def test_disabled_button_is_sold_out(self):
        got = availability.from_regal(
            regal_html([("10:30pm", True), ("7:15pm", False)]), "The Odyssey"
        )
        assert got == {"22:30": "sold_out", "19:15": "seats_available"}

    def test_regal_never_reports_almost_full(self):
        # Regal publishes no "filling up" state, so claiming one would be invented.
        got = availability.from_regal(
            regal_html([(f"{h}:00pm", False) for h in (1, 4, 7)]), "The Odyssey"
        )
        assert set(got.values()) == {"seats_available"}


class TestDispatch:
    def test_supported_chains(self):
        assert availability.supports("amc")
        assert availability.supports("regal")

    def test_cinemark_is_not_supported(self):
        # Its listing is served behind a Cloudflare interstitial, so there is
        # nothing to parse -- not a missing parser.
        assert not availability.supports("cinemark")
        assert availability.parse("cinemark", "<html>Just a moment...</html>", "X") == {}

    def test_empty_html_yields_nothing_rather_than_guessing(self):
        assert availability.parse("amc", "", "The Odyssey") == {}
        assert availability.parse("regal", "", "The Odyssey") == {}


# --------------------------------------------------------------------------- #
# /api/availability
# --------------------------------------------------------------------------- #
def _item(key, chain="regal", theater="regal-hacienda-crossings", hhmm="19:00",
          day="2026-08-01", title="The Odyssey"):
    return {"key": key, "chain": chain, "theater_id": theater,
            "movie_title": title, "start_datetime": f"{day}T{hhmm}:00"}


class TestAvailabilityEndpoint:
    def test_disabled_server_answers_empty_rather_than_erroring(self, monkeypatch):
        # Occupancy is an enrichment: without it the badges stay as they were, so
        # an unavailable verifier is a normal 200 with nothing in it.
        monkeypatch.setattr(get_settings(), "enable_seat_verification", False, raising=False)
        r = TestClient(app).post("/api/availability", json={"showtimes": [_item("a")]})
        assert r.status_code == 200
        assert r.json()["occupancy"] == {}
        assert r.json()["notes"]

    def test_unsupported_chain_is_explained_not_silently_dropped(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "enable_seat_verification", True, raising=False)
        monkeypatch.setattr("app.scrape.verifier.SeatVerifier.available", lambda self: True)
        r = TestClient(app).post(
            "/api/availability",
            json={"showtimes": [_item("c", chain="cinemark", theater="cinemark-oakridge")]},
        )
        body = r.json()
        assert body["occupancy"] == {}
        assert any("cinemark" in n.lower() for n in body["notes"])

    def test_one_listing_load_serves_every_showing_at_that_theatre_and_date(self, monkeypatch):
        # The whole point of reading the listing rather than seat maps: cost scales
        # with theatres x dates, not with showings.
        calls = []

        async def fake(self, groups, *, close_browser=True):
            calls.append(list(groups))
            return {("regal", "regal-hacienda-crossings", "2026-08-01", "19:00"): "sold_out",
                    ("regal", "regal-hacienda-crossings", "2026-08-01", "21:30"): "seats_available"}, []

        monkeypatch.setattr(get_settings(), "enable_seat_verification", True, raising=False)
        monkeypatch.setattr("app.scrape.verifier.SeatVerifier.available", lambda self: True)
        monkeypatch.setattr("app.scrape.verifier.SeatVerifier.occupancy_for", fake)

        r = TestClient(app).post("/api/availability", json={"showtimes": [
            _item("a", hhmm="19:00"), _item("b", hhmm="21:30"), _item("c", hhmm="23:00"),
        ]})
        assert len(calls[0]) == 1, "three showings at one theatre/date must be one group"
        assert r.json()["occupancy"] == {"a": "sold_out", "b": "seats_available"}

    def test_format_decorated_titles_do_not_split_a_theatre_day(self, monkeypatch):
        # Providers decorate titles per format ("The Odyssey - IMAX 70mm IMAX 70mm").
        # Grouping on that once produced duplicate loads and a lookup that failed to
        # match the listing's own film slug; the undecorated title is used instead.
        seen = []

        async def fake(self, groups, *, close_browser=True):
            seen.extend(groups)
            return {}, []

        monkeypatch.setattr(get_settings(), "enable_seat_verification", True, raising=False)
        monkeypatch.setattr("app.scrape.verifier.SeatVerifier.available", lambda self: True)
        monkeypatch.setattr("app.scrape.verifier.SeatVerifier.occupancy_for", fake)

        TestClient(app).post("/api/availability", json={"showtimes": [
            _item("a", hhmm="19:00", title="The Odyssey - IMAX 70mm IMAX 70mm"),
            _item("b", hhmm="21:30", title="The Odyssey"),
        ]})
        assert len(seen) == 1
        assert seen[0][3] == "The Odyssey"
