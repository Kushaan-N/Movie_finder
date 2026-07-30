"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
class TimeWindowRule(BaseModel):
    # Weekday cutoff time in "HH:MM" 24h; showings must start at/after this.
    weekday_cutoff: str = "18:30"
    weekends_unrestricted: bool = True


class SearchRequest(BaseModel):
    movie_title: str = Field(..., min_length=1)
    # `format` remains for compatibility with existing saved searches/clients.
    # New clients use `formats` to select one or more presentation types.
    format: str = "Any"
    formats: list[str] = Field(default_factory=list)
    location: str = Field(..., description="Address or ZIP")
    radius_miles: float = Field(default=25, ge=1, le=200)

    date_from: Optional[date] = None  # defaults applied server-side (today)
    date_to: Optional[date] = None  # defaults applied server-side (today + 14)

    time_rule: TimeWindowRule = Field(default_factory=TimeWindowRule)

    # Seat requirements — NEVER hardcoded in the seat logic; always from here.
    seats_together: int = Field(default=4, ge=1, le=20)
    min_row: int = Field(default=5, ge=1, le=60)


SeatStatus = Literal["match", "check_manually", "no_match"]


class RowInterpretation(BaseModel):
    """Shows how a chain's raw row label maps to a physical row so the user
    can eyeball that the normalization isn't wrong."""

    raw_label: Optional[str] = None
    physical_row: Optional[int] = None
    display: Optional[str] = None  # e.g. "Row H -> physical row 8"


class SeatCheck(BaseModel):
    status: SeatStatus = "check_manually"
    seats_together_requested: int
    min_row_requested: int
    # Best contiguous block we could verify, if any.
    best_block_size: Optional[int] = None
    best_block_row: Optional[RowInterpretation] = None
    reason: Optional[str] = None  # why "check_manually" (canvas, login wall...)


class Showtime(BaseModel):
    key: str  # stable id for diffing (theater+movie+datetime+format)
    theater_id: str
    theater_name: str
    chain: str
    address: Optional[str] = None
    distance_miles: Optional[float] = None
    movie_title: str
    format: str
    start_datetime: datetime
    start_time_label: str  # "7:15 PM"
    booking_url: Optional[str] = None
    seat_check: SeatCheck
    is_new: bool = False  # set on saved-search re-run diff


class SearchMeta(BaseModel):
    provider_used: str
    theaters_considered: int
    showtimes_returned: int
    notes: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    meta: SearchMeta
    showtimes: list[Showtime]


# --------------------------------------------------------------------------- #
# Saved searches
# --------------------------------------------------------------------------- #
class SavedSearchCreate(BaseModel):
    name: str = Field(..., min_length=1)
    config: SearchRequest


class SavedSearchOut(BaseModel):
    id: int
    name: str
    config: SearchRequest
    created_at: datetime
    last_run_at: Optional[datetime] = None


class SavedSearchRunResponse(SearchResponse):
    saved_search_id: int
    new_count: int


# --------------------------------------------------------------------------- #
# On-demand seat verification
# --------------------------------------------------------------------------- #
class VerifySeatsRequest(BaseModel):
    chain: str
    theater_id: str
    # The seat page is resolved from the chain's own showtimes listing using
    # theater + start time. booking_url is kept for compatibility but is not used
    # for verification: providers hand back google.com search links, not seat pages.
    start_datetime: datetime
    booking_url: Optional[str] = None
    seats_together: int = Field(default=4, ge=1, le=20)
    min_row: int = Field(default=5, ge=1, le=60)


class SeatGridRow(BaseModel):
    physical_row: int
    raw_label: Optional[str] = None
    seats_available: list[bool]


class VerifySeatsResponse(BaseModel):
    available: bool  # whether verification could actually run on the server
    seat_check: SeatCheck
    grid: list[SeatGridRow] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    reason: Optional[str] = None
