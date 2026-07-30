"""Geometric seat-map extraction (AMC).

AMC's seat map defeats selector-based scraping completely: verified live on
2026-07-29, the page has zero ``<canvas>`` but also zero ``<text>`` elements —
seat labels like "B22" are rendered as SVG ``<path>`` glyph outlines, and there
is no ``data-seat-*`` attribute, row container, or availability class anywhere.
Every selector in the old config matched nothing.

What *is* reliable is layout plus paint:

* each seat is a small ``<svg>`` box, so clustering bounding boxes by ``y``
  recovers rows and sorting by ``x`` recovers left-to-right order;
* availability is encoded in the seat's gradient fill, so resolving the
  referenced gradient's ``stop-color`` values distinguishes an available seat
  (purple ``#555AAA``/``#2A1F63``) from an occupied one (``transparent``).

That is strictly better than labels for our purpose: ``min_row`` is defined in
terms of *physical* distance from the screen, and geometric y-order gives that
directly, without depending on label quirks (skipped I/O rows, removed recliner
rows) that rows.py otherwise has to correct for.

The extraction runs in the page (it needs ``getBoundingClientRect`` and gradient
resolution), so it requires a live browser. The seam is kept narrow: the JS
returns plain JSON, and ``rows_from_extraction`` converts that to
``SeatMapRow``s, so the conversion and all its guard rails are unit-testable
offline.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..providers.base import SeatMapRow
from .seatmap import SeatMapParseResult

logger = logging.getLogger("showtime_finder.geometry")

# Extracts seat geometry + resolved gradient colors from the live page.
# Returns {rows: [[{available, ignored}]], stats: {...}} with rows ordered
# screen-first (top of the map) and seats ordered left-to-right within a row.
EXTRACT_JS = r"""
(cfg) => {
  const norm = (c) => String(c || '').trim().toLowerCase();
  const inList = (c, list) => list.map(norm).includes(norm(c));

  // Resolve a seat <svg>'s fill to its gradient stop-color signature.
  function signature(svg) {
    const p = svg.querySelector('path');
    if (!p) return null;
    const fill = p.getAttribute('fill') || '';
    const m = fill.match(/url\(#(.+)\)/);
    if (!m) return [fill];
    let grad = null;
    try { grad = svg.querySelector('#' + CSS.escape(m[1])); } catch (e) {}
    grad = grad || document.getElementById(m[1]);
    if (!grad) return null;
    return [...grad.querySelectorAll('stop')].map(s => s.getAttribute('stop-color'));
  }

  const seats = [];
  let unrecognized = 0;
  for (const svg of document.querySelectorAll('svg')) {
    const r = svg.getBoundingClientRect();
    if (r.width < cfg.seat_min_px || r.width > cfg.seat_max_px) continue;
    if (r.height < cfg.seat_min_px || r.height > cfg.seat_max_px) continue;
    const sig = signature(svg);
    if (!sig || !sig.length) { unrecognized++; continue; }

    const anyAvail = sig.some(c => inList(c, cfg.available_stop_colors));
    const allOccupied = sig.every(c => inList(c, cfg.occupied_stop_colors));

    // ONLY the seat palette counts as a seat. Page chrome (back/close/collapse
    // icons, the screen arc, logos) is the same size as a seat and would
    // otherwise cluster into phantom rows above the map, shifting every physical
    // row number and silently corrupting min_row.
    if (!anyAvail && !allOccupied) { unrecognized++; continue; }

    seats.push({
      x: r.x + r.width / 2,
      y: r.y + r.height / 2,
      w: r.width,
      available: anyAvail,
    });
  }

  // Cluster by y into rows, screen-first (smallest y = closest to screen).
  seats.sort((a, b) => a.y - b.y);
  const rows = [];
  let cur = [];
  for (const s of seats) {
    if (cur.length && Math.abs(s.y - cur[cur.length - 1].y) > cfg.row_tolerance_px) {
      rows.push(cur); cur = [];
    }
    cur.push(s);
  }
  if (cur.length) rows.push(cur);

  const gapFactor = cfg.aisle_gap_factor || 1.8;
  const out = rows.map(row => {
    row.sort((a, b) => a.x - b.x);
    // Seat pitch for this row, used to spot aisles.
    const deltas = [];
    for (let i = 1; i < row.length; i++) deltas.push(row[i].x - row[i - 1].x);
    const sorted = [...deltas].sort((a, b) => a - b);
    const pitch = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;

    const cells = [];
    row.forEach((s, i) => {
      if (i > 0 && pitch > 0 && (s.x - row[i - 1].x) > pitch * gapFactor) {
        // An aisle: seats either side of it are not adjacent.
        cells.push({available: false, gap: true});
      }
      cells.push({available: s.available, gap: false});
    });
    return cells;
  });

  const flat = out.flat();
  return {
    rows: out,
    stats: {
      seats_found: flat.filter(s => !s.gap).length,
      available_found: flat.filter(s => s.available).length,
      gaps: flat.filter(s => s.gap).length,
      unrecognized: unrecognized,
      canvas: document.querySelectorAll('canvas').length,
    },
  };
}
"""


def rows_from_extraction(
    data: Optional[dict], cfg: dict, page_text: str = ""
) -> SeatMapParseResult:
    """Convert the in-page extraction payload into rows, or decline with a reason.

    Declines (rather than guessing) when the payload is missing, the map looks
    like a login wall, too few seats were found to be a real auditorium, or no
    seat's colour could be classified — the same never-fabricate rule the
    selector-based parser follows.
    """
    if not data or not isinstance(data, dict):
        return SeatMapParseResult(None, reason="Seat extraction returned nothing")

    stats = dict(data.get("stats") or {})
    raw_rows = data.get("rows") or []

    lowered = (page_text or "").lower()
    for marker in cfg.get("login_wall_text") or []:
        if marker.lower() in lowered:
            return SeatMapParseResult(None, reason="login/sign-in wall detected", stats=stats)

    # A "gap" cell is an aisle inserted to break contiguity, not a seat.
    def real_seats(row) -> int:
        return sum(1 for s in row if not s.get("gap"))

    seats_found = sum(real_seats(r) for r in raw_rows)
    if seats_found == 0:
        if stats.get("canvas"):
            return SeatMapParseResult(
                None, reason="Seat map rendered to <canvas> (not parseable)", stats=stats
            )
        return SeatMapParseResult(
            None, reason="No seat elements found (page structure changed or blocked)", stats=stats
        )

    # Only the known seat palette is treated as a seat, so a moved palette shows
    # up here as "too few seats" rather than as a wrongly-empty auditorium.
    min_expected = int(cfg.get("min_seats_expected") or 0)
    if seats_found < min_expected:
        return SeatMapParseResult(
            None,
            reason=(
                f"Only {seats_found} recognizable seats found (expected at least "
                f"{min_expected}); the seat palette or markup likely changed — "
                "re-verify scrape_selectors.json"
            ),
            stats=stats,
        )

    rows: list[SeatMapRow] = []
    available_found = 0
    for row in raw_rows:
        # Clusters with no real seat cannot occur now that only seat-palette
        # elements are collected, but guard anyway: a phantom row above the map
        # would shift every physical row number and silently break min_row.
        if real_seats(row) == 0:
            continue
        seats_available: list[bool] = []
        for cell in row:
            if cell.get("gap"):
                seats_available.append(False)  # an aisle breaks contiguity
                continue
            is_avail = bool(cell.get("available"))
            available_found += is_avail
            seats_available.append(is_avail)
        # Labels are unreadable in this markup; geometric order carries the
        # physical position, which is what rows.py actually needs.
        rows.append(SeatMapRow(raw_label=None, seats_available=seats_available))

    # Report counts over retained seating only, matching the DOM parser's meaning.
    stats["seats_found"] = seats_found
    stats["available_found"] = available_found
    stats["rows_found"] = len(rows)
    if not rows:
        return SeatMapParseResult(None, reason="No seat rows recovered", stats=stats)
    return SeatMapParseResult(rows, reason=None, stats=stats)
