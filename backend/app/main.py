"""FastAPI app: search, saved searches (with diff-on-rerun), and config."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db, init_db
from .models import LOCAL_USER_ID, SavedSearch, User
from .schemas import (
    AvailabilityRequest,
    AvailabilityResponse,
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
    # The occupancy verifier keeps a browser alive between requests; release it.
    from .scrape.verifier import close_shared_verifier

    await close_shared_verifier()


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
    return VerifySeatsResponse(
        available=True, seat_check=check, grid=grid, stats=result.stats,
        reason=result.reason, seat_url=result.seat_url,
    )


@app.post("/api/availability", response_model=AvailabilityResponse)
async def availability(req: AvailabilityRequest) -> AvailabilityResponse:
    """How full each showing is, from the chains' own listing pages.

    Separate from /api/search on purpose. Search must stay fast, and this costs a
    real page load per theatre and date — but *only* per theatre and date, not per
    showing, so a whole results page usually resolves in a handful of loads.
    The UI calls this after results render and fills the badges in.

    Answering nothing is a normal outcome (Cinemark is behind an interstitial, a
    chain may be rate-limiting), so failures come back as notes and an empty map
    rather than an error — the badge simply stays "unknown".
    """
    from .scrape import availability as availability_parser
    from .scrape.verifier import shared_verifier

    # Shared across requests so its TTL listing cache survives: the same theatre
    # and date asked for twice should cost no network the second time.
    verifier = shared_verifier()
    if not verifier.available():
        return AvailabilityResponse(
            occupancy={},
            notes=["Listing lookups need ENABLE_SEAT_VERIFICATION and Playwright."],
        )

    # One group per (chain, theatre, date) — the unit a single listing load answers
    # for, so N showings at a theatre on a date cost one fetch.
    #
    # Deliberately NOT keyed by title. Providers decorate the title per format
    # ("The Odyssey" and "The Odyssey - IMAX 70mm IMAX 70mm" for the same film),
    # which would split one theatre-day into several identical loads, and the
    # decorated variant fails to match the listing's own film slug. The shortest
    # title in a group is the undecorated one, and matching is substring-based, so
    # it identifies the film for every variant.
    groups: dict[tuple[str, str, str], tuple[str, str, datetime, str]] = {}
    unsupported: set[str] = set()
    for item in req.showtimes:
        if not availability_parser.supports(item.chain):
            unsupported.add(item.chain)
            continue
        day = item.start_datetime
        key = (item.chain, item.theater_id, day.strftime("%Y-%m-%d"))
        prev = groups.get(key)
        if prev is None or len(item.movie_title) < len(prev[3]):
            groups[key] = (item.chain, item.theater_id, day, item.movie_title)

    notes: list[str] = []
    found: dict[tuple[str, str, str, str], str] = {}
    if groups:
        found, notes = await verifier.occupancy_for(
            list(groups.values()), close_browser=False
        )

    for chain in sorted(c for c in unsupported if c):
        notes.append(
            f"{chain.title()}'s showtimes listing does not publish how full a "
            "showing is, so those stay unknown until you check seats."
        )

    # Map back onto the caller's own keys, so the UI never has to re-derive them.
    out: dict[str, str] = {}
    for item in req.showtimes:
        state = found.get((
            item.chain, item.theater_id,
            item.start_datetime.strftime("%Y-%m-%d"),
            item.start_datetime.strftime("%H:%M"),
        ))
        if state:
            out[item.key] = state
    return AvailabilityResponse(occupancy=out, notes=notes)


@app.get("/api/seat-bookmarklet")
def seat_bookmarklet(app_url: str = "http://localhost:5173") -> dict:
    """The bookmarklet the user drags to their bookmarks bar.

    ``app_url`` is where the extracted grid is handed back; it must be this app's
    own origin so the browser can navigate to it with the payload in the fragment.
    """
    from .scrape.bookmarklet import FRAGMENT_KEY, build_href

    href = build_href(app_url)
    return {
        "href": href,
        "fragment_key": FRAGMENT_KEY,
        "bytes": len(href),
        "how_to": [
            "Drag the 'Read seats' link onto your bookmarks bar (or bookmark this href).",
            "In the app, click a showtime's booking link to open the chain's site.",
            "Go to that showtime's seat-selection step and wait for the seats to draw.",
            "Click the bookmarklet. The grid is read from the page you are looking at "
            "and handed back to the app, which applies your seats-together and "
            "minimum-row rules.",
        ],
        "why": (
            "Works for every chain, including ones the server cannot read: Regal's "
            "seat page is behind a CAPTCHA and Cinemark's robots.txt disallows "
            "theirs, but neither restriction applies to your own browsing."
        ),
    }


@app.get("/api/seat-bookmarklet/setup", response_class=HTMLResponse)
def seat_bookmarklet_setup(app_url: str = "http://localhost:5173") -> str:
    """A tiny page whose only job is to offer a draggable bookmarklet link.

    A bookmarklet cannot be installed programmatically, and an anchor's href can't
    be set from JSON by the SPA without the browser stripping the javascript: URL,
    so it is served as real HTML here.
    """
    from html import escape

    from .scrape.bookmarklet import build_href

    href = build_href(app_url)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>showtime-finder — seat-check bookmarklet</title>
<style>
 body{{font:15px/1.55 system-ui,sans-serif;max-width:44rem;margin:3rem auto;padding:0 1rem;
      background:#0b1020;color:#e6e8ef}}
 a.bm{{display:inline-block;padding:.6rem 1rem;border-radius:.5rem;background:#5b6cff;
       color:#fff;font-weight:600;text-decoration:none}}
 code{{background:#171c2e;padding:.1rem .35rem;border-radius:.25rem}}
 ol{{padding-left:1.2rem}} li{{margin:.4rem 0}} .muted{{color:#9aa3b8}}
</style></head><body>
<h1>Seat-check bookmarklet</h1>
<p>Drag this onto your bookmarks bar:</p>
<p><a class="bm" href="{escape(href, quote=True)}">Read seats</a></p>
<ol>
 <li>In showtime-finder, open a showtime's booking link.</li>
 <li>Navigate to that showtime's <b>seat selection</b> step and let the seats draw.</li>
 <li>Click <b>Read seats</b>. The grid is read from the page in front of you and
     handed back to the app at <code>{escape(app_url)}</code>.</li>
</ol>
<p class="muted">This reads only the page you already have open. It works for every
chain — including Regal, whose seat page is behind a CAPTCHA, and Cinemark, whose
robots.txt disallows server-side access — because neither applies to your own
browsing. The grid is also copied to your clipboard as a fallback.</p>
</body></html>"""


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
    from .scrape.resolver import chain_from_url, theater_from_url
    from .services.seatcheck import evaluate_rows

    # Attribute the grid from its own URL rather than trusting the page to say.
    # Without labels the physical row is the screen-first index either way, but a
    # known chain/theater lets per-theater row overrides apply and shows the user
    # what the result is filed against.
    chain = req.chain if req.chain not in ("", "unknown", None) else None
    chain = chain or chain_from_url(req.source_url) or "unknown"
    theater_id = req.theater_id if req.theater_id not in ("", "single", None) else None
    theater_id = (
        theater_id
        or theater_from_url(req.source_url, theaters_service.load_theaters())
        or "single"
    )

    rows: list[SeatMapRow] = []
    for line in req.rows:
        seats = [ch == "O" for ch in line]
        if seats:
            # Labels aren't recoverable from a rendered map, and don't need to be:
            # screen-first order IS the physical position min_row is defined on.
            rows.append(SeatMapRow(raw_label=None, seats_available=seats))
    if not rows:
        raise HTTPException(status_code=400, detail="No seat rows in payload")

    check = evaluate_rows(rows, chain, theater_id, req.seats_together, req.min_row)
    grid = [
        SeatGridRow(
            physical_row=normalize_row(
                chain, None, dom_order_index=idx, theater_id=theater_id
            ).physical_row,
            raw_label=None,
            seats_available=row.seats_available,
        )
        for idx, row in enumerate(rows)
    ]
    total = sum(len(r.seats_available) for r in rows)
    free = sum(sum(r.seats_available) for r in rows)
    caution = None
    if total and (free == 0 or free == total):
        caution = (
            "Every seat read as the same state — that is either a genuinely "
            "sold-out (or empty) house, or the reader couldn't tell them apart. "
            "Compare the grid with the map on screen."
        )
    return VerifySeatsResponse(
        available=True,
        seat_check=check,
        grid=grid,
        stats={
            "seats_found": total,
            "available_found": free,
            "rows_found": len(rows),
            "caution": caution,
            "strategy": req.strategy,
            "source": "browser",
            "source_url": req.source_url,
            "chain": chain,
            "theater_id": theater_id,
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
