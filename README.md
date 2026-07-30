# showtime-finder

Search movie showtimes matching **specific criteria** — format, location + radius,
date range, a weekday time cutoff, and **seat requirements** (N seats together at or
behind a minimum physical row) — and get direct booking links.

Built to grow: SQLite + an ORM so moving to Postgres for multi-user is a config change,
and every user-owned row already carries a `user_id` (default `local`) so adding real
auth later is additive, not a rewrite.

![v1](https://img.shields.io/badge/v1-FastAPI%20%2B%20React-blue)

---

## Features

- **Form-first single page**, mobile-first, with sensible defaults pre-filled — hit
  Search without configuring anything, but every field is editable.
- **Seat requirements are first-class**: `seats_together` and `min_row` are wired
  through the UI to the API and into the seat logic — never hardcoded.
- **Row-position normalization** (`backend/app/rows.py`): "minimum row" is interpreted
  as a **physical** position from the screen, not a literal label. Per-chain rules
  (AMC letter-skip-I/O, Cinemark numeric, DOM-order fallback) live in a hand-editable
  `row_mappings.json`. Results show the interpreted mapping (e.g. `Row H → physical
  row 8`) so you can eyeball that a chain's mapping isn't wrong.
- **Graceful data-source fallback**: SerpApi → MovieGlu → Playwright scraper → demo.
- **Saved searches** with one-tap re-run and a **diff view** highlighting showtimes
  that are new since the last run.
- Seat-check status badges: 🟢 match / 🟡 check manually / 🔴 no block — and it
  **never fabricates** a match when a seat map can't be parsed.
- **Playwright seat verification**: reads AMC's real seat map and upgrades "check
  manually" into a real match/no-match with the physical row. Regal yields sold-out
  state only (its seat page is CAPTCHA-gated) and Cinemark none (its robots.txt
  disallows the seat map) — each says which and why. Rate-limited, robots-aware,
  canvas/login-wall safe, with an offline harness to re-verify against real pages.
- **Radius filtering on live data**: SerpApi's per-theater distance is parsed and
  applied to the radius, not just theaters matched to `theaters.json`.
- **Short-TTL search cache** (default 5 min, `SEARCH_CACHE_TTL_SEC`) to conserve the
  SerpApi free-tier quota and speed up repeat/form-tweak searches. Saved-search
  re-runs bypass it so the diff stays honest.
- **Test suite** (`pytest`) covering row normalization, seat logic, SerpApi parsing,
  the time-window rule, radius filtering, and caching.

---

## Project layout

```
showtime-finder/
├── backend/                 FastAPI + SQLAlchemy
│   ├── app/
│   │   ├── main.py          routes: /api/search, /api/saved-searches, /api/config
│   │   ├── models.py        User + SavedSearch (user_id from day one)
│   │   ├── rows.py          per-chain physical-row normalization
│   │   ├── providers/       serpapi / movieglu / scraper / demo
│   │   └── services/        search orchestration, seat check, theaters + geo
│   ├── requirements.txt
│   └── .env.example
├── frontend/                Vite + React, Tailwind, shadcn-style components
├── theaters.json            editable theaters (chain + confirmed formats)
├── row_mappings.json        editable per-chain row label → physical position rules
└── README.md
```

---

## Setup

### 1. Backend (FastAPI)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # installs Playwright too (see below)
cp .env.example .env                      # optional: add SERPAPI_KEY for live data
uvicorn app.main:app --reload --port 8000
```

The DB (`showtime_finder.db`) and the `local` user are created automatically on first
boot. API docs at http://localhost:8000/docs.

Run the tests:

```bash
cd backend && source .venv/bin/activate
pytest
```

> Without any provider key the app runs in **demo mode** (clearly labeled synthetic
> showtimes) so the whole UI — including seat badges and row mapping — is usable
> immediately.

### 2. Frontend (Vite + React)

```bash
cd frontend
npm install
npm run dev                                # http://localhost:5173
```

The dev server proxies `/api/*` to `http://localhost:8000` (override with
`VITE_API_TARGET`). No CORS setup needed for local dev.

### 3. Playwright (for seat verification — pass 2)

Only needed if you enable seat verification (`ENABLE_SEAT_VERIFICATION=true`):

```bash
cd backend && source .venv/bin/activate
playwright install chromium
```

---

## Getting a free SerpApi key (recommended)

1. Sign up at **https://serpapi.com/** (free tier: **250 searches/month**).
2. Copy your key from the dashboard.
3. Paste it into `backend/.env` (the file already exists — it's gitignored):
   ```
   SERPAPI_KEY=your_key_here
   ```
4. Restart uvicorn. `GET /api/health` should report `"serpapi": true`, and searches
   use SerpApi's Google **showtimes** results (structured JSON, most reliable).

> **How quota is spent:** Google only serves its showtimes widget for
> *theater-name* queries, so one search costs **one SerpApi request per theater in
> range** — not one per search. A 25-mile radius over the default `theaters.json`
> is typically 1–3 requests. Two things keep this bounded: the **radius** prefilter
> (only in-range theaters are queried) and `search_cache_ttl_sec` (identical
> searches are served from cache for 5 minutes). Note that narrowing the *format*
> does **not** reduce cost — formats are filtered against what the provider
> actually reports, because `theaters.json` format lists go stale and using them to
> choose which theaters to query silently hid real showtimes. Widening the radius or
> adding theaters increases per-search cost proportionally. Check remaining quota
> at https://serpapi.com/account.
>
> Each response covers the next several days; the app converts each day's relative
> label ("Today", "Fri") to a real date and keeps only those inside your selected
> date range. A bare ZIP (`94103`) works fine as a location — SerpApi resolves it.

**No key? No problem.** With `SERPAPI_KEY` empty the app runs in **demo mode**
(clearly labeled synthetic showtimes) so the entire UI — seat badges, row mapping,
saved searches, diff — is fully usable. Setting a valid key automatically switches
to live data.

Note: SerpApi (and MovieGlu) don't expose seat maps, so their showtimes come back as
**"check manually"** for seats — which is correct, not a bug. Real seat verification
comes from the Playwright per-chain parsers (AMC only; see "Seat verification" below,
implemented in
`backend/app/providers/scraper_provider.py`).

---

## Data-source priority

| Priority | Source | Enable with | Seat maps? |
|---|---|---|---|
| 1 | SerpApi Google Showtimes | `SERPAPI_KEY` | No → "check manually" |
| 2 | MovieGlu | `MOVIEGLU_*` creds | No → "check manually" |
| 3 | Playwright scraper (Fandango/AMC/Regal/Cinemark) | `ENABLE_SCRAPER_FALLBACK=true` | Yes, per-chain parser |
| — | Seat verification (enriches the above) | `ENABLE_SEAT_VERIFICATION=true` | AMC full map; Regal sold-out only — see below |
| 4 | Demo (synthetic) | *auto when nothing above is set* | Yes (fabricated for demo) |

Each falls back to the next when unavailable or empty. The scraper is rate-limited and
respects `robots.txt`.

---

## Seat verification (Playwright)

SerpApi/MovieGlu give showtimes but no seat maps, so their results start as "check
manually". **Seat verification** closes that gap: it renders the chain's own seat
page, reads the map, and upgrades the badge to a real 🟢 match / 🔴 no-match —
honoring `seats_together`, `min_row`, and physical-row normalization.

Turn it on with `ENABLE_SEAT_VERIFICATION=true` in `backend/.env` (+ `playwright
install chromium`).

### What works, per chain — and why

Each chain was checked against its live seat page on **2026-07-29**. They differ
enough that one parser cannot cover them, and two of the three cannot be automated
at all. This is the honest state, not a to-do list:

| Chain | What you get | Why |
|---|---|---|
| **AMC** | ✅ **Full seat map** | Seat page needs no login and its `robots.txt` permits it. No `<canvas>` — but also no seat attributes and **no `<text>` at all** (labels are SVG `<path>` glyphs), so seats are recovered from layout **geometry** plus resolved **gradient fills**. |
| **Regal** | ⚠️ **Sold-out only** | The seat page is behind a Cloudflare **Turnstile CAPTCHA**, so the exact map is unreachable. Its *listing* is reachable and permitted, and publishes sold-out state (`<button disabled aria-label="…, sold out">`) — which settles the question for those shows: zero seats can't seat any group, so they become a real 🔴 no-match. Everything else stays "check manually". |
| **Cinemark** | ❌ **Nothing** | Its markup is the cleanest of the three (`button[available="True\|False"]` inside `.seatRow`), but `robots.txt` explicitly disallows `/TicketSeatMap` — the seat map itself. Its listing carries no capacity hint either. The parser is implemented and tested, and stays disabled; flip `disabled` in `scrape_selectors.json` if that policy ever changes. |

**Neither Regal nor Cinemark can be fixed by better parsing.** One is a CAPTCHA,
the other is the site's stated crawling policy. Both are reported as such — the UI
gets the actual reason, never a vague failure.

### How a showtime becomes a seat map

A provider's `link` is a `google.com/search` URL, not a seat page, so the seat URL
is resolved from the chain's own listing (`app/scrape/resolver.py`): one listing
load per theater/date, cached and shared across that date's showtimes.

  * AMC — `/movie-theatres/<slug>/showtimes?date=…` → `/showtimes/<id>/seats`
  * Regal — `/theatres/<slug>?date=MM-DD-YYYY` → sold-out state only
  * Cinemark — `/theatres/<slug>?showDate=…` → `/TicketSeatMap/?…` *(disallowed)*

On a Regal listing a showtime is attributed to its film via the nearest ancestor
carrying a `/movies/<slug>` link — one listing holds 100+ times across many movies
at overlapping slots, so matching on time alone would report the wrong film.

`chain_slug` in `theaters.json` maps each theater to its path on the chain's site.

### Guarantees

It is rate-limited, capped per search (`SEAT_VERIFICATION_MAX`), caches per URL,
and **never fabricates** a match. It declines with a surfaced reason when the map is
a `<canvas>`, behind a login wall, or when too few seats are recognizable (which is
how a changed gradient palette shows up). `robots.txt` is honored, and when it
can't be read the answer is "don't scrape" rather than a guess — see
`app/robots.py`, which merges *all* `User-agent: *` groups and rejects bot-check
pages that would otherwise parse as "allow everything".

Two ways it runs:
  - **Up-front** during `/api/search` (real providers only — never demo data).
  - **On-demand** via a **"Check seats"** button on any "check manually" card,
    which calls `POST /api/verify-seats` and shows a **seat-grid preview** with each
    row's physical position.

Expect it to be slow: pages are heavy and loads are serialized and rate-limited, so
budget a few seconds per showtime. That's why it's capped.

### Proven end-to-end

`tests/test_e2e_playwright.py` and `tests/test_verify_endpoint.py` drive real
Chromium over locally-served fixtures through the whole flow — listing → seat-URL
resolution → render → extract → seat check — covering both the geometry (AMC) and
DOM (Cinemark) strategies plus the blocked chain. Fixtures mirror markup captured
from the live sites; only the host differs, since the real sites are bot-protected
and shouldn't be hit from tests.

Verified against production directly on 2026-07-29: AMC Metreon 16, Sat Aug 1
10:30 PM returned 186 seats in 9 rows with 45 available — `[20, 18, 7, 0, 0, …]`
front-to-back, matching a screenshot of the same map. The same day, Regal Hacienda
Crossings returned 20 showtimes for The Odyssey with 5 correctly flagged sold out.

### Parsing is config-driven — tune it against a real page

Per-chain extraction config lives in **`scrape_selectors.json`** (hand-editable),
including which strategy each chain uses and the verified evidence behind it. Chain
markup changes, so re-verify offline with the debug harness — save a real
seat-selection page from your browser, then:

```bash
cd backend && source .venv/bin/activate
python -m app.scrape.debug amc /path/to/saved_seatmap.html --seats 4 --min-row 5
```

It prints each parsed row with its physical-row mapping, an availability strip, and
a simulated seat check — so you can confirm the selectors (and the row mapping)
before trusting live results. Example output:

```
  Row H → physical row 8   avail= 5/6  [█████·]
Simulated seat check (seats_together=4, min_row=5): status = match
```

## Row normalization — how to verify / correct a chain

Seat maps label rows inconsistently (letters skipping I/O, numbers, rows removed for
recliner/ADA conversions). "Minimum row" always means **physical position from the
screen**. The safe default when confidence is low: trust the **order** rows are
returned (1st row in the data = physical row 1), regardless of label.

To correct a chain against a real seat map, edit `row_mappings.json`:

```json
{
  "chains": {
    "amc": {
      "strategy": "letter_skip_io",
      "prefer_dom_order": true,
      "overrides": { "amc-metreon-16:H": 8 }   // pin a verified (theater:label) → physical row
    }
  }
}
```

Raw labels are logged so mappings can be spot-checked and refined over time.

---

## Deploying it (sharing beyond localhost)

Because everything goes through SQLAlchemy and each row already has a `user_id`, going
multi-user is incremental:

1. **Database → Postgres.** Change `DATABASE_URL` in the backend env to a Postgres URL
   (e.g. `postgresql+psycopg://...`). No model changes. (Add Alembic for migrations.)
2. **Host the API.** Deploy `backend/` to **Railway**, **Fly.io**, or **Render**
   (a `uvicorn` web process + a managed Postgres add-on). Set the provider keys as env
   vars there.
3. **Host the frontend.** `npm run build` → static assets on **Vercel** / **Netlify**,
   with `VITE_API_BASE` pointing at the deployed API (and add its origin to the
   backend `CORS_ORIGINS`).
4. **Add auth.** Introduce a real auth provider, populate `user_id` per request instead
   of the `local` default, and scope the saved-search queries by the authenticated
   user — the schema is already shaped for it.

---

## API quick reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/search` | Run a search (body = search config incl. `seats_together`, `min_row`) |
| `POST` | `/api/verify-seats` | Verify one showtime's seats on demand → seat check + grid preview |
| `GET` | `/api/config` | Formats, theaters, and seat-verification availability for the UI |
| `GET` | `/api/health` | Which providers are configured |
| `GET` | `/api/saved-searches` | List saved searches |
| `POST` | `/api/saved-searches` | Create/update by name |
| `POST` | `/api/saved-searches/{id}/run` | Re-run with **diff** (`new_count`, `is_new` per showtime) |
| `DELETE` | `/api/saved-searches/{id}` | Delete |
