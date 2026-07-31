"""Seat availability check.

Given seats_together and min_row (both from the request — never hardcoded),
scan a parsed seat map for any run of >= seats_together contiguous available
seats in a PHYSICAL row >= min_row. Row physical position comes from rows.py so
label quirks (letters, skipped I/O, removed recliner rows) are handled.

If no seat map is available/parseable, return "check manually" — never a guess.
"""
from __future__ import annotations

from typing import Optional

from ..providers.base import ProviderShowtime, SeatMapRow
from ..rows import normalize_row
from ..schemas import RowInterpretation, SeatCheck


def _longest_available_run(seats: list[bool]) -> int:
    best = cur = 0
    for available in seats:
        cur = cur + 1 if available else 0
        best = max(best, cur)
    return best


def evaluate_rows(
    seat_rows: list[SeatMapRow],
    chain: str,
    theater_id: str,
    seats_together: int,
    min_row: int,
) -> SeatCheck:
    """Core seat logic over a parsed (non-empty) seat map. Shared by the initial
    provider check and the Playwright seat-verification enrichment."""
    best_block_size = 0
    best_row_interp: Optional[RowInterpretation] = None
    # The best block ANYWHERE, ignoring min_row. Without this the row filter is a
    # dead end: a map with four seats together in row 3 and nothing behind row 5
    # reports a bare "no block", which reads as "this showing is full" when the
    # truth is "your row requirement excluded it". Keeping it lets the UI say which
    # of the two constraints actually failed, and therefore which one to relax.
    best_any_size = 0
    best_any_interp: Optional[RowInterpretation] = None

    for idx, row in enumerate(seat_rows):
        interp = normalize_row(
            chain=chain,
            raw_label=row.raw_label,
            dom_order_index=idx,
            theater_id=theater_id,
        )
        run = _longest_available_run(row.seats_available)
        if run > best_any_size:
            best_any_size = run
            best_any_interp = RowInterpretation(
                raw_label=interp.raw_label,
                physical_row=interp.physical_row,
                display=interp.display,
            )
        if interp.physical_row < min_row:
            continue
        if run > best_block_size:
            best_block_size = run
            best_row_interp = RowInterpretation(
                raw_label=interp.raw_label,
                physical_row=interp.physical_row,
                display=interp.display,
            )

    if best_block_size >= seats_together:
        status = "match"
    elif best_block_size > 0:
        status = "no_match"
    else:
        status = "no_match"

    return SeatCheck(
        status=status,
        seats_together_requested=seats_together,
        min_row_requested=min_row,
        best_block_size=best_block_size or None,
        best_block_row=best_row_interp,
        best_block_any_row_size=best_any_size or None,
        best_block_any_row=best_any_interp,
        reason=None if status == "match" else "No qualifying contiguous block found",
    )


def check_seats(
    st: ProviderShowtime,
    chain: str,
    theater_id: str,
    seats_together: int,
    min_row: int,
) -> SeatCheck:
    if not st.seat_rows:
        return SeatCheck(
            status="check_manually",
            seats_together_requested=seats_together,
            min_row_requested=min_row,
            reason=st.seat_unavailable_reason or "No parseable seat map available",
        )
    return evaluate_rows(st.seat_rows, chain, theater_id, seats_together, min_row)
