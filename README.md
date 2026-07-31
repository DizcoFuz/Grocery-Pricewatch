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

| Store | Primary Source | Fallback | Notes |
|-------|---------------|----------|-------|
| Aldi | Flipp API | HTML scrape | New ads Wed |
| Walmart | Deals/rollbacks API | HTML scrape | No circular; rollbacks filtered to grocery |
| Jewel-Osco | Flipp API | Albertsons API → HTML | Albertsons banner; Wed–Tue |
| Mariano's | Flipp API | HTML scrape | Kroger banner; Wed–Tue |
| Woodman's | OCR (Tesseract) | — | PDF/image circulars; flags "partial" on low confidence |
| Whole Foods | Sales flyer API/HTML | — | Regular + Prime member prices as separate offers |
| Target | Weekly ad JSON + Circle API | HTML scrape | Circle offers require clip (membership) |

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
| Port | `APP_PORT` | `8000` | HTTP port |
| Password | `APP_PASSWORD` | (blank) | Shared password for hosted deployments |
| Refresh time | `REFRESH_TIME` | `07:00` | Daily refresh time |
| Ad-flip days | `AD_FLIP_DAYS` | `wed,sun` | Extra refresh on these weekdays |
| Two-store threshold | DB setting | $5.00 | Min savings to recommend a second store |
| Baseline strategy | DB setting | `was_price` | How to compute non-sale baseline |

## License

MIT
