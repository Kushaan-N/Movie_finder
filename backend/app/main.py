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
    SavedSearchCreate,
    SavedSearchOut,
    SavedSearchRunResponse,
    SearchRequest,
    SearchResponse,
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
@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "serpapi": settings.has_serpapi,
        "movieglu": settings.has_movieglu,
        "scraper_fallback": settings.enable_scraper_fallback,
    }


@app.get("/api/config")
def config() -> dict:
    """Surface editable options to the UI (formats, chains, theaters)."""
    theaters = theaters_service.load_theaters()
    formats = sorted({f for t in theaters for f in t.formats} | {"IMAX", "Dolby", "Standard", "XD", "ScreenX"})
    return {
        "formats": ["Any", *formats],
        "theaters": [
            {"id": t.id, "name": t.name, "chain": t.chain, "formats": t.formats}
            for t in theaters
        ],
        "provider_available": settings.has_serpapi or settings.has_movieglu or settings.enable_scraper_fallback,
    }


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
