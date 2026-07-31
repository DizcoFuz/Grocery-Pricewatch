"""FastAPI application for the Grocery Pricewatch app.

All endpoints, startup hooks (create tables, seed stores/settings),
and APScheduler integration live here.
"""

from __future__ import annotations

import csv
import io
import json
import os
import base64
from datetime import date, datetime, timezone
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session

from app import crud, matching, recommendations, scheduler, seed
from app.database import SessionLocal, get_db, init_db
from app.schemas import (
    BestPricesResponse,
    DashboardBestDeal,
    DashboardResponse,
    ItemCreate,
    ItemImportResult,
    ItemRead,
    ItemUpdate,
    MatchReview,
    MatchWithDetails,
    OfferRead,
    PriceHistoryRead,
    RecommendationsResponse,
    SavingsResponse,
    SettingsBundle,
    SettingUpdate,
    ShoppingListEntry,
    ShoppingListResponse,
    StoreCreate,
    StoreCostDetail,
    StoreRead,
    StoreRefreshResult,
    StoreStatus,
    StoreUpdate,
    WeeklyReportRead,
)


# ---------------------------------------------------------------------------#
# Background refresh status (shared across requests for async refresh)
# ---------------------------------------------------------------------------#

_refresh_status: dict[str, Any] = {
    "running": False,
    "result": None,
    "started_at": None,
    "finished_at": None,
}


# ---------------------------------------------------------------------------#
# Auth middleware for APP_PASSWORD
# ---------------------------------------------------------------------------#


class PasswordAuthMiddleware(BaseHTTPMiddleware):
    """Simple Basic-Auth middleware gated by the APP_PASSWORD env var.

    When APP_PASSWORD is unset/empty, all requests pass through (no auth).
    When set, all ``/api/*`` endpoints (except ``/api/health``) require
    a Basic-Auth header whose password component matches APP_PASSWORD.
    Static assets and non-API paths are always allowed through.
    """

    async def dispatch(self, request, call_next):  # noqa: ANN001
        password = os.environ.get("APP_PASSWORD", "")
        if not password:
            return await call_next(request)
        path = request.url.path
        if path == "/api/health" or not path.startswith("/api"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                if ":" in decoded and decoded.split(":", 1)[1] == password:
                    return await call_next(request)
            except Exception:
                pass
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": "Basic"},
        )


# ---------------------------------------------------------------------------#
# App factory
# ---------------------------------------------------------------------------#


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Lifespan context manager: startup + shutdown (replaces on_event)."""
    # ── Startup ──
    init_db()
    db = SessionLocal()
    try:
        seed.seed_stores(db)
        seed.seed_default_settings(db)
    finally:
        db.close()
    scheduler.start_scheduler()

    yield

    # ── Shutdown ──
    scheduler.stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Grocery Pricewatch API",
        description="Track grocery prices across stores and find the best deals.",
        version="1.0.0",
        lifespan=_lifespan,
    )

    # ── Auth middleware (before CORS so it sees the original request) ──
    app.add_middleware(PasswordAuthMiddleware)

    # ── CORS ──
    # If APP_PASSWORD is set, tighten CORS to not allow all origins.
    app_password = os.environ.get("APP_PASSWORD", "")
    if app_password:
        allow_origins = []  # API is auth-protected; no cross-origin browser access
    else:
        allow_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Register routes
    # ------------------------------------------------------------------ #
    _register_routes(app)

    # ------------------------------------------------------------------ #
    # Mount SPA static files (must be last — catches everything else)
    # ------------------------------------------------------------------ #
    dist_dir = os.environ.get("FRONTEND_DIST", "/app/frontend/dist")
    if os.path.isdir(dist_dir):
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")

    return app


# ---------------------------------------------------------------------------#
# Routes
# ---------------------------------------------------------------------------#


def _do_refresh_all() -> None:
    """Background task: refresh all stores and update the shared status dict."""
    _refresh_status["running"] = True
    _refresh_status["started_at"] = datetime.now(timezone.utc).isoformat()
    _refresh_status["finished_at"] = None
    _refresh_status["result"] = None
    try:
        result = scheduler.refresh_all_stores()
        _refresh_status["result"] = (
            result.model_dump() if hasattr(result, "model_dump") else result
        )
    except Exception as exc:
        _refresh_status["result"] = {"error": str(exc)}
    finally:
        _refresh_status["running"] = False
        _refresh_status["finished_at"] = datetime.now(timezone.utc).isoformat()


def _register_routes(app: FastAPI) -> None:

    # ===================================================================
    # Dashboard
    # ===================================================================

    @app.get("/api/dashboard", response_model=DashboardResponse, tags=["Dashboard"])
    def dashboard(db: Session = Depends(get_db)):
        """Return dashboard data: headline savings, best deals, store statuses, review queue."""
        # Latest weekly report
        report = crud.get_latest_weekly_report(db)
        headline_savings = 0
        headline_store = "—"
        headline_mode = "single"
        if report is not None:
            if report.projected_savings_pair > report.projected_savings_single and report.best_pair_store_ids:
                headline_savings = report.projected_savings_pair
                headline_mode = "pair"
                # Get store names
                store_names = []
                for sid in report.best_pair_store_ids:
                    s = crud.get_store(db, sid)
                    if s:
                        store_names.append(s.name)
                headline_store = " + ".join(store_names) if store_names else "—"
            else:
                headline_savings = report.projected_savings_single
                headline_mode = "single"
                if report.best_single_store_id:
                    s = crud.get_store(db, report.best_single_store_id)
                    if s:
                        headline_store = s.name

        # Best deals: top items by savings vs baseline
        best_deals: list[DashboardBestDeal] = []
        recs = recommendations.compute_recommendations(db)
        if recs.best_single:
            deal_items = sorted(
                (d for d in recs.best_single.details if d.is_sale),
                key=lambda d: d.line_total,
            )
            # P0-2: use the real 3-tier baseline; skip items with no baseline.
            baseline_map: dict[int, int | None] = {}
            for item in crud.get_items(db, active_only=True):
                baseline_map[item.id] = recommendations._get_item_baseline(db, item)
            for d in deal_items[:10]:
                baseline = baseline_map.get(d.item_id)
                if baseline is None:
                    # No derivable baseline → can't compute savings; show 0.
                    savings = 0
                else:
                    savings = max(baseline * d.quantity - d.line_total, 0)
                best_deals.append(
                    DashboardBestDeal(
                        item_name=d.item_name,
                        store_name=recs.best_single.store_name,
                        sale_price=d.line_total,
                        unit_price=d.unit_price,
                        deal_type=d.deal_type,
                        savings_vs_baseline=int(savings),
                    )
                )

        # P0-3: best-prices data (current vs. last best) for the best-deals table.
        best_prices = recommendations.compute_best_prices(db)

        # Store statuses — compute stale/ok/failed/partial dynamically (FR-1.4)
        stores = crud.get_stores(db)
        store_statuses = [
            StoreStatus(
                store_id=s.id,
                name=s.name,
                enabled=s.enabled,
                last_fetch_at=s.last_fetch_at,
                last_fetch_status=scheduler.compute_stale_status(
                    s.last_fetch_at, s.last_fetch_status
                ),
            )
            for s in stores
        ]

        review_count = crud.count_uncertain(db)

        # Banner: warn if >50% of enabled stores failed
        enabled_stores = [s for s in store_statuses if s.enabled]
        banner = None
        if enabled_stores:
            failed_count = sum(1 for s in enabled_stores if s.last_fetch_status == "failed")
            if len(enabled_stores) > 0 and failed_count / len(enabled_stores) > 0.5:
                banner = "Warning: More than half of enabled stores failed to fetch fresh data"

        return DashboardResponse(
            headline_savings=headline_savings,
            headline_store=headline_store,
            headline_mode=headline_mode,
            best_deals=best_deals,
            store_statuses=store_statuses,
            review_queue_count=review_count,
            last_report=WeeklyReportRead.model_validate(report) if report else None,
            banner=banner,
            best_prices=best_prices,
        )

    # ===================================================================
    # Stores
    # ===================================================================

    @app.get("/api/stores", response_model=list[StoreRead], tags=["Stores"])
    def list_stores(db: Session = Depends(get_db)):
        return crud.get_stores(db)

    @app.post("/api/stores", response_model=StoreRead, status_code=201, tags=["Stores"])
    def create_store(store_in: StoreCreate, db: Session = Depends(get_db)):
        if crud.get_store_by_adapter_key(db, store_in.adapter_key):
            raise HTTPException(400, f"Store with adapter_key '{store_in.adapter_key}' already exists")
        return crud.create_store(db, store_in)

    @app.put("/api/stores/{store_id}", response_model=StoreRead, tags=["Stores"])
    def update_store(store_id: int, store_in: StoreUpdate, db: Session = Depends(get_db)):
        store = crud.update_store(db, store_id, store_in)
        if store is None:
            raise HTTPException(404, "Store not found")
        return store

    @app.delete("/api/stores/{store_id}", status_code=204, tags=["Stores"])
    def delete_store(store_id: int, db: Session = Depends(get_db)):
        if not crud.delete_store(db, store_id):
            raise HTTPException(404, "Store not found")
        return

    # ===================================================================
    # Store refresh
    # ===================================================================

    @app.post("/api/stores/{store_id}/refresh", response_model=StoreRefreshResult, tags=["Refresh"])
    def refresh_store(store_id: int, db: Session = Depends(get_db)):
        store = crud.get_store(db, store_id)
        if store is None:
            raise HTTPException(404, "Store not found")
        result = scheduler.refresh_store(db, store_id)
        return result

    @app.post("/api/refresh-all", tags=["Refresh"])
    def refresh_all(background_tasks: BackgroundTasks):
        """Trigger a background refresh of all stores. Returns immediately."""
        if _refresh_status["running"]:
            return {"status": "already_running", "started_at": _refresh_status["started_at"]}
        background_tasks.add_task(_do_refresh_all)
        return {"status": "started"}

    @app.get("/api/refresh-all/status", tags=["Refresh"])
    def refresh_all_status():
        """Poll the status of the most recent background refresh-all."""
        return _refresh_status

    # ===================================================================
    # Items CRUD
    # ===================================================================

    @app.get("/api/items", response_model=list[ItemRead], tags=["Items"])
    def list_items(active: bool | None = None, db: Session = Depends(get_db)):
        if active is not None and active:
            return crud.get_items(db, active_only=True)
        return crud.get_items(db)

    @app.get("/api/items/{item_id}", response_model=ItemRead, tags=["Items"])
    def get_item(item_id: int, db: Session = Depends(get_db)):
        item = crud.get_item(db, item_id)
        if item is None:
            raise HTTPException(404, "Item not found")
        return item

    @app.post("/api/items", response_model=ItemRead, status_code=201, tags=["Items"])
    def create_item(item_in: ItemCreate, db: Session = Depends(get_db)):
        if crud.find_item_by_name(db, item_in.name):
            raise HTTPException(409, f"Item '{item_in.name}' already exists")
        return crud.create_item(db, item_in)

    @app.put("/api/items/{item_id}", response_model=ItemRead, tags=["Items"])
    def update_item(item_id: int, item_in: ItemUpdate, db: Session = Depends(get_db)):
        item = crud.update_item(db, item_id, item_in)
        if item is None:
            raise HTTPException(404, "Item not found")
        return item

    @app.delete("/api/items/{item_id}", status_code=204, tags=["Items"])
    def delete_item(item_id: int, db: Session = Depends(get_db)):
        if not crud.delete_item(db, item_id):
            raise HTTPException(404, "Item not found")
        return

    # ===================================================================
    # Items import / export / template
    # ===================================================================

    @app.post("/api/items/import/csv", response_model=ItemImportResult, tags=["Items Import"])
    def import_items_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
        content = file.file.read().decode("utf-8-sig")
        return crud.import_items_csv(db, content)

    @app.post("/api/items/import/json", response_model=ItemImportResult, tags=["Items Import"])
    def import_items_json(json_body: list[dict[str, Any]] = Body(...), db: Session = Depends(get_db)):
        return crud.import_items_json(db, json.dumps(json_body))

    @app.get("/api/items/export/csv", response_class=PlainTextResponse, tags=["Items Export"])
    def export_items_csv(db: Session = Depends(get_db)):
        csv_text = crud.export_items_csv(db)
        return PlainTextResponse(
            csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=grocery_items.csv"},
        )

    @app.get("/api/items/export/json", tags=["Items Export"])
    def export_items_json(db: Session = Depends(get_db)):
        json_text = crud.export_items_json(db)
        return Response(
            content=json_text,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=grocery_items.json"},
        )

    @app.get("/api/items/template/csv", response_class=PlainTextResponse, tags=["Items Template"])
    def download_template_csv():
        return PlainTextResponse(
            crud.generate_template_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=grocery_template.csv"},
        )

    @app.get("/api/items/template/json", tags=["Items Template"])
    def download_template_json():
        return Response(
            content=crud.generate_template_json(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=grocery_template.json"},
        )

    # ===================================================================
    # Matches review
    # ===================================================================

    @app.get("/api/matches/review", response_model=list[MatchWithDetails], tags=["Matches"])
    def review_queue(db: Session = Depends(get_db)):
        matches = crud.get_uncertain_matches(db)
        result: list[MatchWithDetails] = []
        for m in matches:
            offer = crud.get_match(db, m.id)
            offer_obj = None
            # Need to load the offer directly
            from app.models import Offer, Store

            offer_obj = db.get(Offer, m.offer_id)
            item = crud.get_item(db, m.item_id)
            # Get the store through the offer's ad cycle
            from app.models import AdCycle

            cycle = db.get(AdCycle, offer_obj.ad_cycle_id) if offer_obj else None
            store = crud.get_store(db, cycle.store_id) if cycle else None

            offer_read = OfferRead.model_validate(offer_obj) if offer_obj else OfferRead(
                id=0, ad_cycle_id=0, raw_text="", product_name="", brand="", size_text="",
                price=0, deal_type="", effective_unit_price=0, unit_price_unknown=True,
                requires_membership_or_coupon=False,
            )
            result.append(
                MatchWithDetails(
                    id=m.id,
                    offer_id=m.offer_id,
                    item_id=m.item_id,
                    confidence=m.confidence,
                    status=m.status,
                    decided_by=m.decided_by,
                    offer=offer_read,
                    item_name=item.name if item else "",
                    store_name=store.name if store else "",
                )
            )
        return result

    @app.post("/api/matches/{match_id}/decide", response_model=MatchWithDetails, tags=["Matches"])
    def decide_match(match_id: int, review: MatchReview, db: Session = Depends(get_db)):
        match = crud.review_match(db, match_id, review.decision)
        if match is None:
            raise HTTPException(404, "Match not found")
        # Build response with details
        from app.models import Offer, AdCycle

        offer_obj = db.get(Offer, match.offer_id)
        item = crud.get_item(db, match.item_id)
        cycle = db.get(AdCycle, offer_obj.ad_cycle_id) if offer_obj else None
        store = crud.get_store(db, cycle.store_id) if cycle else None
        offer_read = OfferRead.model_validate(offer_obj) if offer_obj else OfferRead(
            id=0, ad_cycle_id=0, raw_text="", product_name="", brand="", size_text="",
            price=0, deal_type="", effective_unit_price=0, unit_price_unknown=True,
            requires_membership_or_coupon=False,
        )
        return MatchWithDetails(
            id=match.id,
            offer_id=match.offer_id,
            item_id=match.item_id,
            confidence=match.confidence,
            status=match.status,
            decided_by=match.decided_by,
            offer=offer_read,
            item_name=item.name if item else "",
            store_name=store.name if store else "",
        )

    # ===================================================================
    # Recommendations
    # ===================================================================

    @app.get("/api/recommendations", response_model=RecommendationsResponse, tags=["Recommendations"])
    def get_recommendations(db: Session = Depends(get_db)):
        return recommendations.compute_recommendations(db)

    # ===================================================================
    # Best prices (P0-3: current vs. last best — headline feature)
    # ===================================================================

    @app.get("/api/best-prices", response_model=BestPricesResponse, tags=["Best Prices"])
    def get_best_prices(db: Session = Depends(get_db)):
        """Per-item current best offer/store, last best from PriceHistory,
        all-time min, and delta + direction (FR-4.2/4.3)."""
        return recommendations.compute_best_prices(db)

    # ===================================================================
    # Savings
    # ===================================================================

    @app.get("/api/savings", response_model=SavingsResponse, tags=["Savings"])
    def get_savings(db: Session = Depends(get_db)):
        report = crud.get_latest_weekly_report(db)
        weekly_savings = report.projected_savings_single if report else 0
        cumulative = crud.get_cumulative_savings(db)
        history = crud.get_all_weekly_reports(db)
        return SavingsResponse(
            weekly_savings=weekly_savings,
            cumulative_savings=cumulative,
            weekly_report=WeeklyReportRead.model_validate(report) if report else None,
            history=[WeeklyReportRead.model_validate(r) for r in history],
        )

    # ===================================================================
    # Settings
    # ===================================================================

    @app.get("/api/settings", response_model=SettingsBundle, tags=["Settings"])
    def get_settings(db: Session = Depends(get_db)):
        settings = crud.get_all_settings(db)
        return SettingsBundle(
            two_store_threshold=int(settings.get("two_store_threshold", "500")),
            baseline_strategy=settings.get("baseline_strategy", "auto"),
            refresh_schedule=settings.get("refresh_schedule", "07:00"),
        )

    @app.put("/api/settings", response_model=SettingsBundle, tags=["Settings"])
    def update_settings(bundle: SettingsBundle, db: Session = Depends(get_db)):
        updates = {
            "two_store_threshold": str(bundle.two_store_threshold),
            "baseline_strategy": bundle.baseline_strategy,
            "refresh_schedule": bundle.refresh_schedule,
        }
        crud.set_settings(db, updates)
        if "refresh_schedule" in updates:
            scheduler.reschedule()
        result = crud.get_all_settings(db)
        return SettingsBundle(
            two_store_threshold=int(result.get("two_store_threshold", "500")),
            baseline_strategy=result.get("baseline_strategy", "auto"),
            refresh_schedule=result.get("refresh_schedule", "07:00"),
        )

    @app.put("/api/settings/raw", response_model=SettingsBundle, tags=["Settings"])
    def update_settings_raw(settings_list: list[SettingUpdate] = Body(...), db: Session = Depends(get_db)):
        updates = {s.key: s.value for s in settings_list}
        if updates:
            crud.set_settings(db, updates)
            if "refresh_schedule" in updates:
                scheduler.reschedule()
        result = crud.get_all_settings(db)
        return SettingsBundle(
            two_store_threshold=int(result.get("two_store_threshold", "500")),
            baseline_strategy=result.get("baseline_strategy", "auto"),
            refresh_schedule=result.get("refresh_schedule", "07:00"),
        )

    # ===================================================================
    # Item price history
    # ===================================================================

    @app.get("/api/items/{item_id}/history", response_model=list[PriceHistoryRead], tags=["Items"])
    def get_item_history(item_id: int, db: Session = Depends(get_db)):
        if crud.get_item(db, item_id) is None:
            raise HTTPException(404, "Item not found")
        return crud.get_price_history(db, item_id)

    # ===================================================================
    # Shopping list
    # ===================================================================

    @app.get("/api/shopping-list", response_model=ShoppingListResponse, tags=["Shopping List"])
    def get_shopping_list(
        mode: str = Query("single", pattern="^(single|pair)$"),
        db: Session = Depends(get_db),
    ):
        recs = recommendations.compute_recommendations(db)
        entries: list[ShoppingListEntry] = []
        total_cost = 0
        baseline_cost = 0
        store_ids: list[int] = []
        store_names: list[str] = []

        if mode == "pair" and recs.best_pair is not None:
            # Use the best pair
            pair = recs.best_pair
            total_cost = pair.total_cost
            baseline_cost = pair.baseline_cost
            store_ids = pair.store_ids
            store_names = pair.store_names
            store_name_map = dict(zip(pair.store_ids, pair.store_names))
            for d in pair.details:
                chosen_store = pair.item_store_map.get(d.item_id, pair.store_ids[0])
                entries.append(
                    ShoppingListEntry(
                        item_id=d.item_id,
                        item_name=d.item_name,
                        quantity=d.quantity,
                        unit_of_measure="ea",
                        store_id=chosen_store,
                        store_name=store_name_map.get(chosen_store, ""),
                        unit_price=d.unit_price,
                        line_total=d.line_total,
                        is_sale=d.is_sale,
                        deal_type=d.deal_type,
                    )
                )
        elif recs.best_single is not None:
            single = recs.best_single
            total_cost = single.total_cost
            baseline_cost = single.baseline_cost
            store_ids = [single.store_id]
            store_names = [single.store_name]
            for d in single.details:
                entries.append(
                    ShoppingListEntry(
                        item_id=d.item_id,
                        item_name=d.item_name,
                        quantity=d.quantity,
                        unit_of_measure="ea",
                        store_id=single.store_id,
                        store_name=single.store_name,
                        unit_price=d.unit_price,
                        line_total=d.line_total,
                        is_sale=d.is_sale,
                        deal_type=d.deal_type,
                    )
                )
        else:
            raise HTTPException(404, "No recommendations available. Refresh stores first.")

        savings = max(baseline_cost - total_cost, 0)
        return ShoppingListResponse(
            mode=mode,
            total_cost=total_cost,
            baseline_cost=baseline_cost,
            savings=savings,
            store_ids=store_ids,
            store_names=store_names,
            entries=entries,
        )

    # ===================================================================
    # Health check (for Docker)
    # ===================================================================

    @app.get("/api/health", tags=["Health"])
    def health():
        return {"status": "ok"}


# ---------------------------------------------------------------------------
# App instance (for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
