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
- **Playwright seat verification** (AMC/Regal/Cinemark): renders the booking page,
  parses the seat map via config-driven selectors, and upgrades "check manually"
  into a real match/no-match — with an offline debug harness to tune selectors
  against real pages. Rate-limited, robots-aware, canvas/login-wall safe.
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

1. Sign up at **https://serpapi.com/** (free tier: **100 searches/month**).
2. Copy your key from the dashboard.
3. Paste it into `backend/.env` (the file already exists — it's gitignored):
   ```
   SERPAPI_KEY=your_key_here
   ```
4. Restart uvicorn. `GET /api/health` should report `"serpapi": true`, and searches
   use SerpApi's Google **showtimes** results (structured JSON, most reliable).

> **Location tip:** SerpApi resolves showtimes best with a city/state location
> (e.g. `San Jose, California`) rather than a bare ZIP. A single call returns the
> next several days; the app converts each day's relative label to a real date and
> keeps only those inside your selected date range.

**No key? No problem.** With `SERPAPI_KEY` empty the app runs in **demo mode**
(clearly labeled synthetic showtimes) so the entire UI — seat badges, row mapping,
saved searches, diff — is fully usable. Setting a valid key automatically switches
to live data.

Note: SerpApi (and MovieGlu) don't expose seat maps, so their showtimes come back as
**"check manually"** for seats — which is correct, not a bug. Real seat verification
arrives via the Playwright per-chain parsers (pass 2, scaffolded in
`backend/app/providers/scraper_provider.py`).

---

## Data-source priority

| Priority | Source | Enable with | Seat maps? |
|---|---|---|---|
| 1 | SerpApi Google Showtimes | `SERPAPI_KEY` | No → "check manually" |
| 2 | MovieGlu | `MOVIEGLU_*` creds | No → "check manually" |
| 3 | Playwright scraper (Fandango/AMC/Regal/Cinemark) | `ENABLE_SCRAPER_FALLBACK=true` | Yes, per-chain parser |
| 4 | Demo (synthetic) | *auto when nothing above is set* | Yes (fabricated for demo) |

Each falls back to the next when unavailable or empty. The scraper is rate-limited and
respects `robots.txt`.

---

## Seat verification (Playwright, pass 2)

SerpApi/MovieGlu give showtimes but no seat maps, so their results are "check
manually" for seats. **Seat verification** closes that gap: for supported chains
(AMC, Regal, Cinemark) it renders the booking page with Playwright, parses the
seat map, and upgrades the badge to a real 🟢 match / 🔴 no-match — honoring
`seats_together`, `min_row`, and the per-chain physical-row normalization.

- Turn it on: `ENABLE_SEAT_VERIFICATION=true` in `backend/.env` (+ `playwright
  install chromium`). It's rate-limited, respects `robots.txt`, is capped per
  search (`SEAT_VERIFICATION_MAX`), and caches per URL. It **never fabricates** a
  match — if the map is a `<canvas>`, behind a login wall, or the markers can't be
  read, it stays "check manually" with the reason surfaced.
- It enriches showtimes we already have booking URLs for (it does **not** re-scrape
  showtime discovery), which is the reliable, high-value part.

### Parsing is config-driven — tune it against a real page

The CSS selectors that read each chain's seat map live in **`scrape_selectors.json`**
(hand-editable). Chain markup changes, so verify/tune them offline with the debug
harness — save a real seat-selection page from your browser, then:

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
| `GET` | `/api/config` | Formats + theaters for the UI |
| `GET` | `/api/health` | Which providers are configured |
| `GET` | `/api/saved-searches` | List saved searches |
| `POST` | `/api/saved-searches` | Create/update by name |
| `POST` | `/api/saved-searches/{id}/run` | Re-run with **diff** (`new_count`, `is_new` per showtime) |
| `DELETE` | `/api/saved-searches/{id}` | Delete |
