"""Presentation-format matching.

Formats form a hierarchy, not a flat set of strings: a "70mm IMAX" showing *is*
an IMAX showing and *is* a 70mm showing. Exact string equality gets this wrong
in the direction that matters most — a user who checks "IMAX" and is shown
nothing, while the theater is in fact running the movie in 70mm IMAX.

We model that with token subsumption: a requested format matches an actual
format when the requested format's tokens are a subset of the actual's.

    requested "IMAX"      -> {imax}        matches "70mm IMAX" {70mm, imax}  ✓
    requested "70mm"      -> {70mm}        matches "70mm IMAX"               ✓
    requested "70mm IMAX" -> {70mm, imax}  does NOT match plain "IMAX"       ✓
    requested "IMAX"                       does NOT match "Dolby"            ✓

The asymmetry is deliberate: asking for a broad format accepts the premium
variants, but asking for a specific one does not silently downgrade you.
"""
from __future__ import annotations

import re

_ANY = {"", "any"}


def _tokens(fmt: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^a-z0-9]+", (fmt or "").lower()) if t)


def format_matches(requested: str, actual: str) -> bool:
    """Whether an actual showing format satisfies a single requested format."""
    if (requested or "").strip().lower() in _ANY:
        return True
    req, act = _tokens(requested), _tokens(actual)
    if not req:
        return True
    if not act:
        return False
    return req <= act


def format_matches_any(actual: str, requested: list[str]) -> bool:
    """Whether an actual format satisfies any of the requested formats.

    An empty requested list means "Any" and matches everything.
    """
    if not requested:
        return True
    return any(format_matches(r, actual) for r in requested)


def theater_may_have(theater_formats: list[str], requested: list[str]) -> bool:
    """Whether a theater's advertised formats *could* satisfy the request.

    Used only as a hint. theaters.json format lists are hand-maintained and go
    stale, so this must never be the sole reason a theater is skipped — see
    services.search for why the provider is asked about every in-radius theater.
    """
    if not requested:
        return True
    return any(format_matches_any(tf, requested) for tf in theater_formats)
