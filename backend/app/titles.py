"""Movie-title matching.

Chains and Google decorate titles differently ("The Odyssey", "Dune: Part Two",
"Spider-Man: Brand New Day" vs. a slug like "spiderman-brand-new-day"), so a
requested title is compared on significant tokens rather than by string equality.
A subset match in either direction counts, which lets a user type "Odyssey" and
still match "The Odyssey".
"""
from __future__ import annotations

import re

# Words that carry no signal when comparing titles.
STOPWORDS = {"the", "a", "an"}


def tokens(title: str) -> set[str]:
    words = re.split(r"[^a-z0-9]+", (title or "").lower())
    out = {w for w in words if w and w not in STOPWORDS}
    return out or {w for w in words if w}


def titles_match(requested: str, actual: str) -> bool:
    """Whether ``actual`` refers to the same film as ``requested``."""
    req, act = tokens(requested), tokens(actual)
    if not req or not act:
        return False
    return req <= act or act <= req


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def slug_matches_title(requested: str, slug: str) -> bool:
    """Whether a URL slug refers to the same film as ``requested``.

    Slugs need looser matching than titles, in two ways seen on real pages:
    they drop internal punctuation ("Spider-Man" -> ``spiderman``, so token
    matching fails on spider/man), and they carry a trailing chain id
    (``the-odyssey-ho00019072``). Comparing punctuation-stripped strings by
    containment handles both.

    Falls back to token matching for very short titles, where a bare substring
    would match almost anything.
    """
    req, sl = _squash(requested), _squash(slug)
    if not req or not sl:
        return False
    if len(req) >= 4:
        return req in sl or sl in req
    return titles_match(requested, slug)
