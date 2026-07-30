"""Where to send someone for a specific showtime.

A provider's own link is a ``google.com/search`` URL — a search results page, not
somewhere you can see that showtime. That makes a "check manually" badge a dead
end. These build real destinations instead.

Three tiers, best first:

1. **The chain's own page for that theatre and date** — deterministic, so it costs
   nothing and cannot go stale: every URL shape here was verified against the live
   site on 2026-07-29, and ``chain_slug`` in theaters.json supplies the path. This
   lands on the right theatre showing the right day's showtimes, on the site that
   actually sells the ticket.
2. **The exact showtime** — needs one fetch of the chain's listing to learn the
   showtime's own id, so it is resolved on demand rather than for every row of a
   search. See ``scrape.resolver``.
3. **Fandango** — a genuine cross-chain fallback, and the only option for a theatre
   with no ``chain_slug``. Note it is a *search* link: Fandango's own deep links
   embed internal ids (``/the-odyssey-2026-236162/movie-times``) that cannot be
   derived, so pretending to link straight to a showtime there would be a guess.
   The query is scoped to the movie and theatre so the search lands usefully.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus

# Human-facing name for the "open at the chain" action.
CHAIN_LABELS = {
    "amc": "AMC",
    "regal": "Regal",
    "cinemark": "Cinemark",
}

FANDANGO_SEARCH = "https://www.fandango.com/search?q={q}"


def fandango_url(movie_title: str, theater_name: str = "") -> str:
    """A Fandango search scoped to this movie (and theatre, when known)."""
    q = " ".join(part for part in (movie_title or "", theater_name or "") if part).strip()
    return FANDANGO_SEARCH.format(q=quote_plus(q or "movies"))


def chain_url(chain: str, chain_slug: str, start: datetime) -> Optional[str]:
    """The chain's own showtimes page for this theatre on this date.

    Deterministic — no network call. Returns None when we have no slug for the
    theatre, in which case Fandango is the fallback.
    """
    from .scrape import resolver

    if not chain_slug:
        return None
    return resolver.listing_url((chain or "").lower(), chain_slug, start)


def build(
    *,
    chain: str,
    chain_slug: str,
    movie_title: str,
    theater_name: str,
    start: datetime,
    provider_url: Optional[str] = None,
) -> dict:
    """Assemble every destination we can offer for one showtime."""
    direct = chain_url(chain, chain_slug, start)
    return {
        "chain": direct,
        "chain_label": CHAIN_LABELS.get((chain or "").lower()),
        "fandango": fandango_url(movie_title, theater_name),
        # The provider's search link, kept because it is occasionally the only
        # thing that knows about an unusual venue.
        "search": provider_url,
        # Best single destination, for a plain "open this" action.
        "best": direct or fandango_url(movie_title, theater_name),
    }
