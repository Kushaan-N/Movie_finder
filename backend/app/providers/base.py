"""Provider interface + shared types.

A provider turns a search into a flat list of ``ProviderShowtime`` records.
Seat-map data is optional: providers that can supply a parseable seat map fill
``seat_rows`` so the seat check can run; providers that can't leave it None and
the result becomes "check manually" (never fabricated).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class SeatMapRow:
    """One row of a seat map, in DOM/API order (index 0 = closest to screen)."""

    raw_label: Optional[str]
    # available seats as booleans in physical left->right order; True = free.
    seats_available: list[bool]


@dataclass
class ProviderShowtime:
    theater_name: str
    movie_title: str
    format: str
    start_datetime: datetime
    theater_address: Optional[str] = None
    chain: Optional[str] = None
    booking_url: Optional[str] = None
    # Distance from the searched location, when the provider reports it
    # (SerpApi does). Lets radius filtering work on live data, not just on
    # theaters matched to theaters.json.
    distance_miles: Optional[float] = None
    # Present only when a seat map could be parsed (scraper pass 2).
    seat_rows: Optional[list[SeatMapRow]] = None
    # If the seat map could not be parsed reliably, why.
    seat_unavailable_reason: Optional[str] = None


@dataclass
class ProviderQuery:
    movie_title: str
    fmt: str
    location: str
    date_from: date
    date_to: date
    theaters: list = field(default_factory=list)  # list[services.theaters.Theater]


class ShowtimeProvider(ABC):
    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider is configured/usable right now."""

    @abstractmethod
    async def fetch(self, query: ProviderQuery) -> list[ProviderShowtime]:
        ...
