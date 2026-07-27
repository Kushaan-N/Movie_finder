"""Demo provider (lowest priority).

Activates ONLY when no real provider (SerpApi / MovieGlu / scraper) is
configured, so the app is usable and the full UI — including seat-check badges
and physical-row display — can be demonstrated without any API keys. Results
are clearly flagged (provider_used = "demo") and are synthetic, not real
showtimes. It is deterministic (seeded off the query, no wall-clock randomness)
so re-runs are stable for the saved-search diff view.

Set any real provider to disable this automatically.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from ..config import get_settings
from .base import ProviderQuery, ProviderShowtime, SeatMapRow, ShowtimeProvider

# A few showings per day per theater at these clock times.
_SLOT_TIMES = [time(13, 0), time(16, 30), time(19, 15), time(21, 45)]


def _seat_map(seed: int, rows: int = 12, cols: int = 16) -> list[SeatMapRow]:
    """Build a deterministic seat map. Some back rows have a full contiguous
    block free; front rows are patchier — enough to exercise match / no_match."""
    labels = "ABCDEFGHJKLMNPQR"  # skip I/O like AMC
    out: list[SeatMapRow] = []
    for r in range(rows):
        avail: list[bool] = []
        for c in range(cols):
            # Pseudo-random but deterministic occupancy.
            occupied = ((seed * 31 + r * 7 + c * 13) % 5) == 0
            # Guarantee a clean block in rows >= 6 (physical) for demo matches.
            if r >= 6 and 4 <= c <= 10:
                occupied = False
            avail.append(not occupied)
        out.append(SeatMapRow(raw_label=labels[r % len(labels)], seats_available=avail))
    return out


class DemoProvider(ShowtimeProvider):
    name = "demo"

    def available(self) -> bool:
        s = get_settings()
        # Only when nothing real is configured.
        return not (s.has_serpapi or s.has_movieglu or s.enable_scraper_fallback)

    async def fetch(self, query: ProviderQuery) -> list[ProviderShowtime]:
        out: list[ProviderShowtime] = []
        theaters = query.theaters or []
        day = query.date_from
        seed = 0
        while day <= query.date_to:
            for t in theaters:
                fmts = t.formats or ["Standard"]
                if query.fmt and query.fmt.lower() not in ("any", ""):
                    if query.fmt not in fmts:
                        continue
                    fmts = [query.fmt]
                for slot in _SLOT_TIMES:
                    seed += 1
                    fmt = fmts[seed % len(fmts)]
                    start = datetime.combine(day, slot)
                    out.append(
                        ProviderShowtime(
                            theater_name=t.name,
                            theater_address=t.address,
                            movie_title=query.movie_title,
                            format=fmt,
                            start_datetime=start,
                            chain=t.chain,
                            booking_url=f"{t.booking_base_url}/showtimes?demo=1",
                            # Give roughly half the showings a parseable seat map
                            # so both "match" (green) and "check manually"
                            # (yellow) badges appear in the demo.
                            seat_rows=_seat_map(seed) if seed % 2 == 0 else None,
                            seat_unavailable_reason=None if seed % 2 == 0 else "Demo: seat map not parsed for this showing",
                        )
                    )
            day += timedelta(days=1)
        return out
