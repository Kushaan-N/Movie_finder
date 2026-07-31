"""Ranking chains by what they can tell us about seats, and ordering by it.

The seat requirement is the whole point of the app, so a theatre that can answer
it is more useful than one that can't — even a nearer one. These tests pin that
ordering, and pin the two failure modes it must never blur: "the site forbids
this" versus "we could not read the site's rules".
"""
import time

import pytest

from app.providers import scraper_provider
from app.providers.scraper_provider import cache_robots, robots_unreadable
from app.scrape.verifier import (
    SEAT_DATA_FULL,
    SEAT_DATA_NONE,
    SEAT_DATA_PARTIAL,
    seat_data_rank,
)


@pytest.fixture(autouse=True)
def _clean_robots_state():
    scraper_provider._robots_cache.clear()
    scraper_provider._robots_unreadable.clear()
    yield
    scraper_provider._robots_cache.clear()
    scraper_provider._robots_unreadable.clear()


class TestSeatDataRank:
    def test_amc_yields_a_full_map(self):
        assert seat_data_rank("amc") == SEAT_DATA_FULL

    def test_regal_is_partial(self):
        # Its seat page is CAPTCHA-gated; only sold-out state survives.
        assert seat_data_rank("regal") == SEAT_DATA_PARTIAL

    def test_cinemark_yields_nothing(self):
        # robots.txt disallows its seat map, so nothing without the user's browser.
        assert seat_data_rank("cinemark") == SEAT_DATA_NONE

    def test_ranks_order_full_before_partial_before_none(self):
        assert SEAT_DATA_FULL < SEAT_DATA_PARTIAL < SEAT_DATA_NONE

    def test_unknown_chain_is_not_promoted_above_amc(self):
        # An unconfigured chain must never outrank one we can actually read.
        assert seat_data_rank("some-new-chain") >= SEAT_DATA_FULL


class TestOrdering:
    def test_a_far_full_map_theatre_outranks_a_near_unreadable_one(self):
        """The behaviour change: capability first, distance second."""
        rows = [
            ("cinemark", 0.5),
            ("regal", 25.4),
            ("amc", 4.2),
        ]
        rows.sort(key=lambda r: (seat_data_rank(r[0]), r[1]))
        assert [c for c, _ in rows] == ["amc", "regal", "cinemark"]

    def test_distance_still_decides_within_a_tier(self):
        rows = [("amc", 12.0), ("amc", 0.9), ("amc", 4.2)]
        rows.sort(key=lambda r: (seat_data_rank(r[0]), r[1]))
        assert [d for _, d in rows] == [0.9, 4.2, 12.0]


class TestRobotsUnreadableIsNotAPolicy:
    def test_a_queue_page_is_recorded_as_unreadable(self):
        # AMC serves a Queue-it waiting room in place of robots.txt under load.
        assert cache_robots("https://x.test", "You are now in line\nless than a minute") is False
        assert robots_unreadable("https://x.test") is True

    def test_a_real_robots_file_is_not_flagged(self):
        assert cache_robots("https://y.test", "User-agent: *\nDisallow: /private") is True
        assert robots_unreadable("https://y.test") is False

    def test_a_site_that_genuinely_denies_all_is_not_called_unreadable(self):
        # Deny-all is a decision. Reporting it as "we couldn't read the file"
        # would be just as wrong as the reverse.
        assert cache_robots("https://z.test", "User-agent: *\nDisallow: /") is True
        assert robots_unreadable("https://z.test") is False

    def test_unreadable_expires_so_one_bad_minute_is_not_permanent(self):
        cache_robots("https://x.test", "You are now in line")
        assert robots_unreadable("https://x.test") is True
        scraper_provider._robots_unreadable["https://x.test"] = (
            time.time() - scraper_provider._ROBOTS_RETRY_AFTER_SEC - 1
        )
        assert robots_unreadable("https://x.test") is False
        # The deny-all verdict is dropped too, so the next check re-fetches
        # instead of inheriting it.
        assert "https://x.test" not in scraper_provider._robots_cache
