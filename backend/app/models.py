"""SQLAlchemy models.

Every user-owned row carries a ``user_id`` from day one (defaulting to the
single ``local`` user) so adding real auth later is purely additive — no schema
migration of existing tables, just start populating a real user id.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

LOCAL_USER_ID = "local"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, default="Local User")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    saved_searches: Mapped[list["SavedSearch"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_saved_search_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), default=LOCAL_USER_ID, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Full search request payload, stored as JSON text so schema evolves freely.
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Snapshot of showtime keys from the previous run, for the diff view.
    last_result_keys_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="saved_searches")
