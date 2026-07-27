"""Seat-map HTML parser.

Turns rendered seat-selection HTML into ``SeatMapRow`` records in DOM order
(top of the map = closest to the screen = physical row 1), driven entirely by
the hand-editable ``scrape_selectors.json``. This is the real, testable core of
the Playwright fallback: the Playwright side just fetches the HTML, this parses
it, so the parsing logic can be unit-tested offline against saved pages.

Design principles:
  * DOM order is authoritative for physical position (rows.py refines from here).
  * Never fabricate a match. If the map is a <canvas>, behind a login wall, or
    the availability markers can't be recognized, return ``rows=None`` with a
    reason so the result becomes "check manually".
  * A seat is only "available" (True) when positively marked so; unknown seats
    are treated as unavailable, and non-seat gaps break contiguity.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from bs4 import BeautifulSoup

from ..config import get_settings
from ..providers.base import SeatMapRow

logger = logging.getLogger("showtime_finder.seatmap")


@dataclass
class SeatMapParseResult:
    rows: Optional[list[SeatMapRow]]
    reason: Optional[str] = None
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.rows)


@lru_cache
def _load_selectors() -> dict:
    path = get_settings().scrape_selectors_file
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - config error
        logger.warning("Could not load scrape_selectors.json (%s).", exc)
        return {"chains": {}}


def _chain_cfg(chain: str) -> Optional[dict]:
    return _load_selectors().get("chains", {}).get((chain or "").lower())


def _lower_set(values) -> set[str]:
    return {str(v).lower() for v in (values or [])}


def _classify(el, cfg: dict) -> tuple[str, bool]:
    """Return (state, recognized) for a seat element.

    state ∈ {"available", "unavailable", "ignore"}. ``recognized`` is True when
    we matched an explicit availability/space marker (as opposed to falling back
    to a default), which lets the caller detect "selectors didn't match anything"
    and decline rather than guess.
    """
    classes = {c.lower() for c in (el.get("class") or [])}

    if classes & _lower_set(cfg.get("ignore_class_any")):
        return "ignore", True

    attr = cfg.get("available_attr")
    val = (el.get(attr) or "").strip().lower() if attr else ""
    if val:
        if val in _lower_set(cfg.get("unavailable_values")):
            return "unavailable", True
        if val in _lower_set(cfg.get("available_values")):
            return "available", True

    if classes & _lower_set(cfg.get("unavailable_class_any")):
        return "unavailable", True
    if classes & _lower_set(cfg.get("available_class_any")):
        return "available", True

    if cfg.get("available_is_default"):
        return "available", False
    return "unavailable", False


def _row_label(row_el, cfg: dict, seat_els: list) -> Optional[str]:
    attr = cfg.get("row_label_attr")
    if attr and row_el.get(attr):
        return str(row_el.get(attr)).strip()
    sel = cfg.get("row_label_selector")
    if sel:
        label_el = row_el.select_one(sel)
        if label_el and label_el.get_text(strip=True):
            return label_el.get_text(strip=True)
    # Fall back to a row attribute carried on the first seat.
    seat_row_attr = cfg.get("seat_row_attr")
    if seat_row_attr and seat_els and seat_els[0].get(seat_row_attr):
        return str(seat_els[0].get(seat_row_attr)).strip()
    return None


def _detect_wall(soup: BeautifulSoup, cfg: dict) -> Optional[str]:
    sel = cfg.get("login_wall_selector")
    if sel and soup.select_one(sel):
        return "login/sign-in wall detected"
    texts = cfg.get("login_wall_text") or []
    if texts:
        page_text = soup.get_text(" ", strip=True).lower()
        for marker in texts:
            if marker.lower() in page_text:
                return "login/sign-in wall detected"
    return None


def parse_seat_html(chain: str, html: str) -> SeatMapParseResult:
    """Parse seat-selection HTML into rows. See module docstring for guarantees."""
    cfg = _chain_cfg(chain)
    if not cfg:
        return SeatMapParseResult(None, reason=f"No seat-map selectors configured for chain '{chain}'")
    if not html or not html.strip():
        return SeatMapParseResult(None, reason="Empty page")

    soup = BeautifulSoup(html, "html.parser")

    wall = _detect_wall(soup, cfg)
    if wall:
        return SeatMapParseResult(None, reason=wall)

    row_els = soup.select(cfg.get("row_selector", "")) if cfg.get("row_selector") else []
    seat_selector = cfg.get("seat_selector", "")

    rows: list[SeatMapRow] = []
    total_seats = 0
    total_available = 0
    total_recognized = 0

    def add_row(label, seat_els):
        nonlocal total_seats, total_available, total_recognized
        seats_available: list[bool] = []
        for seat in seat_els:
            state, recognized = _classify(seat, cfg)
            if state == "ignore":
                seats_available.append(False)  # gap breaks contiguity
                continue
            total_seats += 1
            if recognized:
                total_recognized += 1
            is_avail = state == "available"
            if is_avail:
                total_available += 1
            seats_available.append(is_avail)
        if seats_available:
            rows.append(SeatMapRow(raw_label=label, seats_available=seats_available))

    # Preferred path: explicit row containers, read top-to-bottom (DOM order).
    if row_els:
        for row_el in row_els:
            seat_els = row_el.select(seat_selector) if seat_selector else []
            add_row(_row_label(row_el, cfg, seat_els), seat_els)

    # Fallback: no usable rows from containers (flat layout, or row_selector
    # matched the seats themselves) — group flat seats by their row attribute in
    # first-appearance (DOM) order.
    if not rows and seat_selector:
        flat_seats = soup.select(seat_selector)
        seat_row_attr = cfg.get("seat_row_attr")
        grouped: dict[str, list] = {}
        order: list[str] = []
        for seat in flat_seats:
            key = str(seat.get(seat_row_attr) or "") if seat_row_attr else ""
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(seat)
        for key in order:
            add_row(key or None, grouped[key])

    stats = {
        "rows_found": len(rows),
        "seats_found": total_seats,
        "available_found": total_available,
        "recognized_seats": total_recognized,
    }

    if not rows or total_seats == 0:
        # Distinguish canvas rendering from a plain structure change.
        if cfg.get("canvas_selector") and soup.select_one(cfg["canvas_selector"]):
            return SeatMapParseResult(None, reason="Seat map rendered to <canvas> (not parseable)", stats=stats)
        return SeatMapParseResult(None, reason="No seat elements found (page structure changed or blocked)", stats=stats)

    # We found seats but couldn't recognize a single availability marker — almost
    # certainly the selectors need tuning; decline rather than report all-taken.
    if total_recognized == 0:
        return SeatMapParseResult(
            None,
            reason="Could not determine seat availability (status markers not recognized — tune selectors)",
            stats=stats,
        )

    return SeatMapParseResult(rows, reason=None, stats=stats)
