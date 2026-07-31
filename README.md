# Grocery Pricewatch

A web application that tracks weekly ad sale prices at your grocery stores, compares prices for a regularly purchased item list, highlights the current best price per item, recommends the optimal store(s) to shop at, and reports total savings.

## Features

- **Weekly ad tracking** for 7 store chains: Aldi, Walmart, Jewel-Osco, Mariano's, Woodman's, Whole Foods, Target
- **Item list management** with CSV/JSON import/export and dedup
- **Automatic offer matching** with confidence scoring and a review queue for uncertain matches
- **Best-price comparison** with per-item history and sparkline charts
- **Store recommendations** — single best store and best two-store combination
- **Savings reporting** — weekly projected savings + cumulative total
- **Responsive dashboard** — usable on a phone in-store

## Quick Start

```bash
# 1. Clone
git clone https://github.com/DizcoFuz/Grocery-Pricewatch.git
cd Grocery-Pricewatch

# 2. Configure
cp .env.example .env
# Edit .env — set APP_PORT, APP_PASSWORD, etc.

# 3. Build & run
docker compose up -d --build

# 4. Open
open http://localhost:8000
```

The app starts with 7 default stores (disabled by default) and an empty item list. Enable stores on the Stores page, add items (or import the CSV template), then hit **Refresh now**.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser  ──▶  :8000  FastAPI (serves API + React SPA)    │
│                       │                                   │
│  ┌─────────┐  ┌───────┴────────┐  ┌─────────────────┐    │
│  │ SQLite  │  │ Store Adapters │  │ APScheduler     │    │
│  │ /data/  │  │ (7 chains)     │  │ (daily 07:00)   │    │
│  └─────────┘  └───────┬────────┘  └─────────────────┘    │
│                       │                                   │
│              ┌────────┼────────┐                          │
│              ▼        ▼        ▼                          │
│         Browserless  Tesseract  httpx                     │
│         (headless)   (OCR)       (API fetch)               │
└──────────────────────────────────────────────────────────┘
```

### Services

| Service | Image | Purpose |
|---------|-------|---------|
| `app` | `python:3.12-slim` (custom) | FastAPI backend + React frontend + APScheduler |
| `browserless` | `ghcr.io/browserless/chromium:latest` | Headless Chrome for JS-rendered ad pages |
| `tesseract` | `hertzg/tesseract-server:latest` | OCR for image/PDF circulars (Woodman's) |

### Store Adapter Data Sources

Each store uses a different acquisition strategy, documented per adapter:

| Store | Primary Source | Fallback | Verified | Notes |
|-------|---------------|----------|----------|-------|
| Aldi | Flipp API | HTML scrape | Not yet verified — mock fixture | New ads Wed |
| Walmart | Deals/rollbacks API | HTML scrape | Not yet verified — mock fixture | No circular; rollbacks filtered to grocery |
| Jewel-Osco | Flipp API | Albertsons API → HTML | Not yet verified — mock fixture | Albertsons banner; Wed–Tue |
| Mariano's | Flipp API | HTML scrape | Not yet verified — mock fixture | Kroger banner; Wed–Tue |
| Woodman's | OCR (Tesseract) | — | Not yet verified — mock fixture | PDF/image circulars; flags "partial" on low confidence |
| Whole Foods | Sales flyer API/HTML | — | Not yet verified — mock fixture | Regular + Prime member prices as separate offers |
| Target | Weekly ad JSON + Circle API | HTML scrape | Not yet verified — mock fixture | Circle offers require clip (membership) |

**Fixture tests:** Each adapter has a recorded fixture test under `backend/tests/test_adapters.py` that loads a mock JSON fixture (matching the expected API response format derived from the adapter source code) and verifies the parser correctly extracts offers, prices, deal types, and metadata. Fixtures are mock-based — no adapter has been verified against a live store endpoint yet. When a live fetch is performed, replace the mock fixture with the sanitized real response and drop the `_mock_` infix from the filename.

**ToS note:** Scraping retail sites may violate their Terms of Service. Prefer official/partner APIs (e.g., Kroger public API, Flipp) where available. The app rate-limits politely (≥2s between requests per host), caches aggressively, and identifies the client honestly. Stores where only ToS-problematic access exists are flagged in the adapter docstrings.

## Development

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev   # Vite dev server on :5173, proxies /api to :8000
```

### Database

SQLite with WAL mode at `/data/grocery.db`. All prices stored as integer cents. Schema auto-created on startup. Raw ad payloads archived at `/data/raw_payloads/` with 90-day retention.

## Adding a Store

1. Create `backend/app/adapters/my_store.py` inheriting from `StoreAdapter`
2. Implement `fetch_current_ad()` returning `(list[OfferData], AdMetadata)`
3. Register it in `backend/app/adapters/__init__.py`
4. Add it to `backend/app/seed.py`

## Configuration

All settings are in the DB (editable via UI Settings page) or `.env`:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| Port | `APP_PORT` | `9010` | HTTP port (docker01 range 9001-9049) |
| Default ZIP | `DEFAULT_ZIP` | `60601` | ZIP code for initial store seeding (editable per-store in UI) |
| Password | `APP_PASSWORD` | (blank) | Shared password for hosted deployments |
| Refresh time | `REFRESH_TIME` | `07:00` | Daily refresh time |
| Ad-flip days | `AD_FLIP_DAYS` | `wed,sun` | Extra refresh on these weekdays |
| Two-store threshold | DB setting | $5.00 | Min savings to recommend a second store |
| Baseline strategy | DB setting | `was_price` | How to compute non-sale baseline |

## Known Limitations (v1)

- **P2-5 — Brand-boost hardening:** The `extract_brand` heuristic may misidentify generic descriptors (e.g., "USDA Choice") as brands. The boost only increases match score (it doesn't override keyword matching), so impact is limited, but the heuristic could be tightened by requiring brand tokens to appear in the offer text.
- **P2-10 — Realized savings ("shopped it" checkbox):** FR-6.2 is an optional nice-to-have that is not yet implemented. The shopping list has checkboxes in the UI but they are not persisted or used to compute realized savings vs. projected savings.
- **Adapter verification:** Adapter endpoints have been researched and updated to use plausible real APIs (Flipp `backflipp.wishabi.com`, Target `redsky.target.com`, etc.), but no adapter has been verified against a live store endpoint. Fixture-based parser tests exist under `backend/tests/fixtures/` but are mock-based. See the "Verified" column in the adapter table above.
- **Docker compose build:** Not yet verified end-to-end from a clean checkout (R-5 from re-review). The Dockerfile and compose file are structurally complete, but `docker compose up --build` should be run on a host with Docker to verify: healthchecks pass, SPA loads at `/`, auth engages when `APP_PASSWORD` is set, and data survives `down`/`up`.
- **Healthcheck removal:** The tesseract and browserless containers ship no shell or curl, so CMD-based healthchecks always fail. The compose file uses `depends_on: service_started` instead of `service_healthy` for these services. The app container retains its own healthcheck (the one that matters for acceptance criterion 10).

## License

MIT
