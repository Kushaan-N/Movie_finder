"""One seat-map extractor, shared by the server and the bookmarklet.

There used to be two: a narrow server-side one keyed on a hardcoded gradient
palette, and the chain-agnostic ``seat_extract.js`` written for the bookmarklet.
The hardcoded palette was the problem — it was verified at AMC Metreon 16, and AMC
themes auditoriums differently, so at Eastridge 15 the same code reported **155
seats, 0 available, 83 unrecognized** for a map that was largely open. A verdict
of "no seats anywhere" that confidently contradicts the screen is worse than no
verdict.

``seat_extract.js`` doesn't have that failure mode: it reads which colour means
"free" from the page's own legend, and falls back to painted-vs-unpainted and then
saturation. It also covers explicit attributes and enabled/disabled controls, so
one extractor now serves every chain and every route, and improvements land in
both places at once.

This module owns the Python side: loading that JS and turning its payload into a
``SeatMapParseResult``, keeping the same never-fabricate guarantees as before.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..providers.base import SeatMapRow
from .seatmap import SeatMapParseResult

logger = logging.getLogger("showtime_finder.extract")

_JS_PATH = Path(__file__).with_name("seat_extract.js")


@lru_cache
def extractor_js() -> str:
    """The shared extractor, as a bare function expression."""
    return _JS_PATH.read_text(encoding="utf-8").strip()


def call_expression(options_json: str = "{}") -> str:
    """A ``page.evaluate``-ready expression that runs the extractor."""
    return f"({extractor_js()})({options_json})"


def rows_from_payload(
    data: Optional[dict], cfg: dict, page_text: str = ""
) -> SeatMapParseResult:
    """Convert the extractor's payload into rows, or decline with a reason.

    Declines rather than guesses when the payload is missing, the page looks like a
    login wall, or the extractor itself refused — it already enforces a minimum
    auditorium size, drops clusters that aren't seat rows, and prefers a strategy
    that distinguished two states over one that called everything the same.
    """
    if not data or not isinstance(data, dict):
        return SeatMapParseResult(None, reason="Seat extraction returned nothing")

    stats = dict(data.get("stats") or {})
    stats["strategy"] = data.get("strategy")

    lowered = (page_text or "").lower()
    for marker in cfg.get("login_wall_text") or []:
        if marker.lower() in lowered:
            return SeatMapParseResult(None, reason="login/sign-in wall detected", stats=stats)

    if not data.get("ok"):
        if stats.get("canvas"):
            return SeatMapParseResult(
                None, reason="Seat map rendered to <canvas> (not parseable)", stats=stats
            )
        return SeatMapParseResult(
            None,
            reason=data.get("reason")
            or (
                "The seat map didn't load in time — the chain's site was slow or "
                "showed a waiting room. Try again, or open the seat page directly."
            ),
            stats=stats,
        )

    rows: list[SeatMapRow] = []
    for row in data.get("rows") or []:
        seats = [
            False if cell.get("gap") else bool(cell.get("available"))
            for cell in row
        ]
        if seats:
            # Labels aren't recoverable from a rendered map and aren't needed:
            # screen-first order IS the physical position min_row is defined on.
            rows.append(SeatMapRow(raw_label=None, seats_available=seats))

    if not rows:
        return SeatMapParseResult(None, reason="No seat rows recovered", stats=stats)

    if stats.get("uniform"):
        # Accepted, but say so: a map read as entirely one state is either a real
        # sold-out/empty house or a signal the extractor couldn't split.
        stats["caution"] = (
            "Every seat read as the same state — check this against the map on screen."
        )
    stats["rows_found"] = len(rows)
    return SeatMapParseResult(rows, reason=None, stats=stats)
