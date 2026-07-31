"""How full a showing is, read from the chain's own showtimes listing.

This exists because "seats unknown" was the honest answer far too often. Reading
a *seat map* is expensive and mostly blocked: it costs one page load per showing,
Regal's seat page is behind a CAPTCHA and Cinemark's is disallowed by robots.txt.
But the **listing** page — one load per theatre and date, which seat verification
already fetches and caches — labels every showing on it:

* AMC writes the state into the showtime link's own text: "6:00pm Almost Full".
* Regal marks sold-out showings semantically: ``<button disabled
  aria-label="10:30pm showtime, sold out">``.

So a single listing load answers "is there anything left here at all" for every
showing at that theatre that day, at no extra cost.

**What this is not.** Occupancy is not the seat check. "Almost full" does not say
whether 4 seats remain *together*, and "seats available" does not either — only a
seat map answers that, which is what ``Check seats`` is still for. The one case
occupancy settles outright is sold out: no seats at all means no block of N, so
that is reported as a real no-match rather than a hint.

Cinemark is absent on purpose: its listing is served behind a Cloudflare
interstitial ("Just a moment…"), so there is nothing to parse. Its seats stay a
browser-assisted job.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from ..titles import slug_matches_title

# Ordered worst-to-best; a showing is described by exactly one of these.
Occupancy = Literal["sold_out", "almost_full", "seats_available"]

# Only chains whose listing actually carries the signal.
SUPPORTED = {"amc", "regal"}

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.IGNORECASE)

_SOLD_OUT = re.compile(r"\bsold\s*out\b", re.IGNORECASE)
# AMC's wording for "open, but filling up". Kept as alternatives rather than one
# loose pattern so an unrelated phrase can't be read as an occupancy claim.
_ALMOST_FULL = re.compile(r"\b(almost\s*full|few\s*seats\s*(left|remaining)?|selling\s*fast)\b",
                          re.IGNORECASE)


def normalize_time(label: str) -> Optional[str]:
    """A showtime label reduced to 24-hour "HH:MM", or None if it has no time.

    Listings write times as "6:00pm", "6:00 PM", "6:00 p.m."; the same showing has
    to key identically however it was written, since this is what maps a parsed
    row back to the showtime it belongs to.
    """
    m = _TIME_RE.search(label or "")
    if not m:
        return None
    hour = int(m.group(1)) % 12
    if m.group(3).lower() == "p":
        hour += 12
    return f"{hour:02d}:{int(m.group(2)):02d}"


def _state_from_text(text: str) -> Occupancy:
    if _SOLD_OUT.search(text):
        return "sold_out"
    if _ALMOST_FULL.search(text):
        return "almost_full"
    return "seats_available"


def from_amc(html: str, movie_title: str) -> dict[str, Occupancy]:
    """AMC: the state is part of the showtime link's text.

    A listing carries every film playing that day, so showings are attributed to
    their own film via the nearest ``/movies/<slug>`` ancestor link — matching on
    time alone once resolved a request for one film to another film's showing.
    """
    from .resolver import amc_candidates

    out: dict[str, Occupancy] = {}
    for label, _seat_url in amc_candidates(html, movie_title=movie_title):
        key = normalize_time(label)
        if key:
            out[key] = _state_from_text(label)
    return out


def from_regal(html: str, movie_title: str) -> dict[str, Occupancy]:
    """Regal: sold-out is explicit, and everything else is only "not sold out".

    Regal does not publish a "filling up" state, so a showing it hasn't marked is
    reported as ``seats_available`` — which claims nothing beyond "some seat
    exists", exactly what the page supports.
    """
    from .resolver import regal_sold_out

    out: dict[str, Occupancy] = {}
    for label, sold in regal_sold_out(html, movie_title):
        key = normalize_time(label)
        if key:
            out[key] = "sold_out" if sold else "seats_available"
    return out


def parse(chain: str, html: str, movie_title: str) -> dict[str, Occupancy]:
    """Occupancy by normalized "HH:MM" for one film at one theatre on one date."""
    if not html:
        return {}
    if chain == "amc":
        return from_amc(html, movie_title)
    if chain == "regal":
        return from_regal(html, movie_title)
    return {}


def supports(chain: str) -> bool:
    return chain in SUPPORTED


__all__ = ["Occupancy", "SUPPORTED", "normalize_time", "parse", "supports",
           "from_amc", "from_regal"]
