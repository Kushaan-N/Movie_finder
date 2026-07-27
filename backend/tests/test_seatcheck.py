"""Seat availability check: honors seats_together + min_row, never fabricates."""
from datetime import datetime

from app.providers.base import ProviderShowtime, SeatMapRow
from app.services.seatcheck import check_seats


def _st(seat_rows=None, reason=None):
    return ProviderShowtime(
        theater_name="AMC Test",
        movie_title="Movie",
        format="IMAX",
        start_datetime=datetime(2026, 7, 26, 19, 0),
        chain="amc",
        seat_rows=seat_rows,
        seat_unavailable_reason=reason,
    )


def test_no_seat_map_is_check_manually_never_a_guess():
    res = check_seats(_st(seat_rows=None, reason="canvas"), "amc", "amc-test", seats_together=4, min_row=5)
    assert res.status == "check_manually"
    assert res.reason == "canvas"


def test_match_when_block_meets_size_and_row():
    # 12 rows; put a clean 4-block only in a back row (dom index 6 -> physical 7).
    rows = []
    for i in range(12):
        seats = [False] * 10
        if i == 6:
            seats = [True, True, True, True, False, False, False, False, False, False]
        rows.append(SeatMapRow(raw_label=chr(65 + i), seats_available=seats))
    res = check_seats(_st(seat_rows=rows), "amc", "amc-test", seats_together=4, min_row=5)
    assert res.status == "match"
    assert res.best_block_size >= 4
    assert res.best_block_row.physical_row == 7


def test_no_match_when_block_too_small():
    rows = [SeatMapRow(raw_label=chr(65 + i), seats_available=[True, True, False, True, True]) for i in range(10)]
    res = check_seats(_st(seat_rows=rows), "amc", "amc-test", seats_together=4, min_row=5)
    assert res.status == "no_match"
    assert res.best_block_size == 2  # longest run is 2


def test_rows_in_front_of_min_row_are_ignored():
    # A full free block, but only in dom index 0 (physical row 1) < min_row 5.
    rows = [SeatMapRow(raw_label="A", seats_available=[True] * 8)]
    rows += [SeatMapRow(raw_label=chr(66 + i), seats_available=[False] * 8) for i in range(6)]
    res = check_seats(_st(seat_rows=rows), "amc", "amc-test", seats_together=4, min_row=5)
    assert res.status == "no_match"  # the big block was too close to the screen


def test_seats_together_is_request_driven_not_hardcoded():
    rows = [SeatMapRow(raw_label=chr(65 + i), seats_available=[True] * 6) for i in range(10)]
    # Same seat map, different requested block sizes.
    assert check_seats(_st(seat_rows=rows), "amc", "t", seats_together=6, min_row=5).status == "match"
    assert check_seats(_st(seat_rows=rows), "amc", "t", seats_together=7, min_row=5).status == "no_match"
