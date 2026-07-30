"""robots.txt parsing that matches how real sites actually write it.

Python's ``urllib.robotparser`` gets two things wrong for the theater chains, both
verified against live files on 2026-07-29, and both in the unsafe direction:

1. **Repeated groups are dropped.** ``_add_entry`` keeps only the *first*
   ``User-agent: *`` group. cinemark.com writes one directive per group — nine
   separate ``User-agent: *`` blocks — so everything after the first was ignored,
   including ``Disallow: /TicketSeatMap``. We would have scraped a path the site
   explicitly forbids. Here, all groups applicable to the agent are merged.

2. **Non-robots responses parse as "allow everything".** amctheatres.com answers
   ``/robots.txt`` with an HTML bot-check page; feeding that to the parser yields
   zero rules, which reads as unrestricted. ``parse`` rejects content that isn't
   robots.txt so the caller can fail closed instead.

Matching follows the de-facto standard: the longest matching rule wins, ``Allow``
beats ``Disallow`` on an equal-length tie, ``*`` is a wildcard and ``$`` anchors
the end. An empty ``Disallow:`` means "allow all" for that group.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urlparse


class NotRobotsTxt(ValueError):
    """The fetched body is not a robots.txt file (e.g. an HTML challenge page)."""


@dataclass
class _Group:
    agents: set[str] = field(default_factory=set)
    rules: list[tuple[str, bool]] = field(default_factory=list)  # (path, allowed)


def _looks_like_html(text: str) -> bool:
    head = text.lstrip().lower()[:400]
    return head.startswith(("<!doctype", "<html", "<?xml")) or "<head" in head or "<script" in head


def _to_regex(pattern: str) -> re.Pattern:
    """Translate a robots path pattern (with * and $) into a prefix-match regex."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        else:
            out.append(re.escape(ch))
    return re.compile("".join(out) + ("$" if anchored else ""))


@dataclass
class RobotsFile:
    groups: list[_Group] = field(default_factory=list)
    # True when the file was readable but contained no usable directives.
    empty: bool = False

    def _applicable(self, agent: str) -> list[_Group]:
        agent = (agent or "*").lower()
        # Prefer groups naming our agent; fall back to the wildcard groups.
        named = [g for g in self.groups if any(a in agent or agent in a for a in g.agents if a != "*")]
        if named:
            return named
        return [g for g in self.groups if "*" in g.agents]

    def can_fetch(self, agent: str, url: str) -> bool:
        path = urlparse(url).path or "/"
        query = urlparse(url).query
        if query:
            path = f"{path}?{query}"
        path = unquote(path)

        best_len = -1
        best_allowed = True
        for group in self._applicable(agent):
            for pattern, allowed in group.rules:
                if not pattern:
                    # "Disallow:" with an empty value means allow everything.
                    continue
                if not _to_regex(pattern).match(path):
                    continue
                length = len(pattern)
                # Longest match wins; Allow wins an equal-length tie.
                if length > best_len or (length == best_len and allowed):
                    best_len, best_allowed = length, allowed
        return True if best_len < 0 else best_allowed


def parse(body: str) -> RobotsFile:
    """Parse robots.txt text. Raises NotRobotsTxt for non-robots content."""
    if body is None:
        raise NotRobotsTxt("empty body")
    text = body.lstrip("﻿")  # cinemark.com serves a UTF-8 BOM
    if _looks_like_html(text):
        raise NotRobotsTxt("response is HTML, not robots.txt")

    groups: list[_Group] = []
    current: Optional[_Group] = None
    saw_directive = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()

        if field_name == "user-agent":
            # Consecutive user-agent lines share one group.
            if current is None or current.rules:
                current = _Group()
                groups.append(current)
            current.agents.add(value.lower())
            saw_directive = True
        elif field_name in ("allow", "disallow"):
            if current is None:
                continue  # a rule before any user-agent line: ignore
            current.rules.append((value, field_name == "allow"))
            saw_directive = True
        # sitemap / crawl-delay / host are irrelevant here

    rf = RobotsFile(groups=[g for g in groups if g.agents])
    rf.empty = not saw_directive
    return rf
