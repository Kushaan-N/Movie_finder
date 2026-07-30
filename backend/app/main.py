"""FastAPI app: search, saved searches (with diff-on-rerun), and config."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db, init_db
from .models import LOCAL_USER_ID, SavedSearch, User
from .schemas import (
    GridSeatCheckRequest,
    SavedSearchCreate,
    SavedSearchOut,
    SavedSearchRunResponse,
    SearchRequest,
    SearchResponse,
    SeatCheck,
    SeatGridRow,
    VerifySeatsRequest,
    VerifySeatsResponse,
)
from .services import theaters as theaters_service
from .services.search import run_search

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("showtime_finder")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Ensure the single local user exists (multi-user auth is additive later).
    from .database import SessionLocal

    with SessionLocal() as db:
        if not db.get(User, LOCAL_USER_ID):
            db.add(User(id=LOCAL_USER_ID, display_name="Local User"))
            db.commit()
    yield


app = FastAPI(title="showtime-finder", version="1.0.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Health / config
# --------------------------------------------------------------------------- #
def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


@app.get("/api/health")
def health() -> dict:
    pw = _playwright_installed()
    return {
        "status": "ok",
        "serpapi": settings.has_serpapi,
        "movieglu": settings.has_movieglu,
        "scraper_fallback": settings.enable_scraper_fallback,
        "seat_verification": settings.enable_seat_verification and pw,
        "playwright_installed": pw,
    }


@app.get("/api/config")
def config() -> dict:
    """Surface editable options to the UI (formats, chains, theaters)."""
    from .scrape.verifier import verifiable_chains

    theaters = theaters_service.load_theaters()
    preferred_formats = ["IMAX", "Dolby", "70mm IMAX", "70mm", "4DX", "ScreenX", "XD", "Standard"]
    available_formats = {f for t in theaters for f in t.formats}
    formats = preferred_formats + sorted(available_formats - set(preferred_formats))
    return {
        "formats": ["Any", *formats],
        "theaters": [
            {"id": t.id, "name": t.name, "chain": t.chain, "formats": t.formats}
            for t in theaters
        ],
        "provider_available": settings.has_serpapi or settings.has_movieglu or settings.enable_scraper_fallback,
        "seat_verification": settings.enable_seat_verification and _playwright_installed(),
        "verify_chains": sorted(verifiable_chains()),
    }


@app.post("/api/verify-seats", response_model=VerifySeatsResponse)
async def verify_seats(req: VerifySeatsRequest) -> VerifySeatsResponse:
    """Verify one showtime's seats on demand (renders the booking page + parses)."""
    from .rows import normalize_row
    from .scrape.seatmap import _chain_cfg
    from .scrape.verifier import SeatVerifier, _unavailable_reason, verifiable_chains

    def _cannot(reason: str, available: bool) -> VerifySeatsResponse:
        return VerifySeatsResponse(
            available=available,
            seat_check=SeatCheck(
                status="check_manually",
                seats_together_requested=req.seats_together,
                min_row_requested=req.min_row,
                reason=reason,
            ),
            reason=reason,
        )

    verifier = SeatVerifier()
    if not verifier.available():
        return _cannot("Seat verification is not enabled on the server (ENABLE_SEAT_VERIFICATION).", False)
    if req.chain not in verifiable_chains():
        cfg = _chain_cfg(req.chain) or {}
        return _cannot(
            _unavailable_reason(cfg)
            or f"No seat-map parser configured for chain '{req.chain}'.",
            True,
        )

    check, result = await verifier.verify_showtime(
        req.chain, req.theater_id, req.start_datetime, req.seats_together, req.min_row,
        movie_title=req.movie_title or "",
    )
    grid: list[SeatGridRow] = []
    if result.ok:
        for idx, row in enumerate(result.rows):
            interp = normalize_row(req.chain, row.raw_label, dom_order_index=idx, theater_id=req.theater_id)
            grid.append(
                SeatGridRow(
                    physical_row=interp.physical_row,
                    raw_label=interp.raw_label,
                    seats_available=row.seats_available,
                )
            )
    return VerifySeatsResponse(available=True, seat_check=check, grid=grid, stats=result.stats, reason=result.reason)


@app.post("/api/verify-seats/from-grid", response_model=VerifySeatsResponse)
def verify_seats_from_grid(req: GridSeatCheckRequest) -> VerifySeatsResponse:
    """Run the seat check against a map the user read in their own browser.

    This is the path that works for every chain. It needs no scraping, no
    credentials and no CAPTCHA: the bookmarklet reads the page the user already
    has open and hands the grid here, where the same evaluate_rows/normalize_row
    logic used by the automated path turns it into a verdict.
    """
    from .providers.base import SeatMapRow
    from .rows import normalize_row
    from .services.seatcheck import evaluate_rows

    rows: list[SeatMapRow] = []
    for line in req.rows:
        seats = [ch == "O" for ch in line]
        if seats:
            # Labels aren't recoverable from a rendered map, and don't need to be:
            # screen-first order IS the physical position min_row is defined on.
            rows.append(SeatMapRow(raw_label=None, seats_available=seats))
    if not rows:
        raise HTTPException(status_code=400, detail="No seat rows in payload")

    check = evaluate_rows(rows, req.chain, req.theater_id, req.seats_together, req.min_row)
    grid = [
        SeatGridRow(
            physical_row=normalize_row(
                req.chain, None, dom_order_index=idx, theater_id=req.theater_id
            ).physical_row,
            raw_label=None,
            seats_available=row.seats_available,
        )
        for idx, row in enumerate(rows)
    ]
    total = sum(len(r.seats_available) for r in rows)
    return VerifySeatsResponse(
        available=True,
        seat_check=check,
        grid=grid,
        stats={
            "seats_found": total,
            "available_found": sum(sum(r.seats_available) for r in rows),
            "rows_found": len(rows),
            "strategy": req.strategy,
            "source": "browser",
            "source_url": req.source_url,
        },
    )


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
@app.post("/api/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    return await run_search(req)


# --------------------------------------------------------------------------- #
# Saved searches
# --------------------------------------------------------------------------- #
def _to_out(s: SavedSearch) -> SavedSearchOut:
    return SavedSearchOut(
        id=s.id,
        name=s.name,
        config=SearchRequest.model_validate_json(s.config_json),
        created_at=s.created_at,
        last_run_at=s.last_run_at,
    )


@app.get("/api/saved-searches", response_model=list[SavedSearchOut])
def list_saved(db: Session = Depends(get_db)) -> list[SavedSearchOut]:
    rows = db.scalars(
        select(SavedSearch).where(SavedSearch.user_id == LOCAL_USER_ID).order_by(SavedSearch.created_at)
    ).all()
    return [_to_out(r) for r in rows]


@app.post("/api/saved-searches", response_model=SavedSearchOut)
def create_saved(payload: SavedSearchCreate, db: Session = Depends(get_db)) -> SavedSearchOut:
    existing = db.scalar(
        select(SavedSearch).where(
            SavedSearch.user_id == LOCAL_USER_ID, SavedSearch.name == payload.name
        )
    )
    if existing:
        # Upsert by name so re-saving the same name updates the config.
        existing.config_json = payload.config.model_dump_json()
        db.commit()
        db.refresh(existing)
        return _to_out(existing)

    row = SavedSearch(
        user_id=LOCAL_USER_ID,
        name=payload.name,
        config_json=payload.config.model_dump_json(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@app.delete("/api/saved-searches/{search_id}")
def delete_saved(search_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(SavedSearch, search_id)
    if not row or row.user_id != LOCAL_USER_ID:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(row)
    db.commit()
    return {"deleted": search_id}


@app.post("/api/saved-searches/{search_id}/run", response_model=SavedSearchRunResponse)
async def run_saved(search_id: int, db: Session = Depends(get_db)) -> SavedSearchRunResponse:
    from datetime import datetime, timezone

    row = db.get(SavedSearch, search_id)
    if not row or row.user_id != LOCAL_USER_ID:
        raise HTTPException(status_code=404, detail="Saved search not found")

    req = SearchRequest.model_validate_json(row.config_json)
    # Bypass the cache so a re-run truly re-fetches and the diff reflects reality.
    result = await run_search(req, use_cache=False)

    # Diff against the previous run's keys to flag new showtimes.
    prev_keys: set[str] = set()
    if row.last_result_keys_json:
        try:
            prev_keys = set(json.loads(row.last_result_keys_json))
        except json.JSONDecodeError:
            prev_keys = set()

    new_count = 0
    for st in result.showtimes:
        # First-ever run (no snapshot yet) => nothing is "new".
        st.is_new = bool(prev_keys) and st.key not in prev_keys
        if st.is_new:
            new_count += 1

    # Persist the new snapshot + run time.
    row.last_result_keys_json = json.dumps([s.key for s in result.showtimes])
    row.last_run_at = datetime.now(timezone.utc)
    db.commit()

    return SavedSearchRunResponse(
        meta=result.meta,
        showtimes=result.showtimes,
        saved_search_id=search_id,
        new_count=new_count,
    )
