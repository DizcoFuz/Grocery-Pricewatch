"""APScheduler integration for daily ad refresh.

- Daily refresh at 07:00 local (configurable via settings)
- Per-store refresh on known ad-flip days (Wed for Aldi/Jewel/Mariano's, Sun for Target, etc.)
- refresh_all_stores(): call each enabled store adapter, save offers, run matching,
  update price history, compute recommendations + weekly report
- Per-store status tracking
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app import crud, matching, recommendations
from app.database import SessionLocal
from app.models import AdCycle, Item, Match, Offer, Store
from app.schemas import StoreRefreshResult, RefreshAllResult, WeeklyReportRead

logger = logging.getLogger(__name__)

# Scheduler singleton
_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
# Ad-flip day mapping (0=Mon … 6=Sun)
# Imported from seed module
# ---------------------------------------------------------------------------

AD_FLIP_DAYS: dict[str, int] = {
    "aldi": 2,         # Wednesday
    "walmart": 5,      # Saturday
    "jewel_osco": 2,   # Wednesday
    "marianos": 2,     # Wednesday
    "woodmans": 2,     # Wednesday
    "whole_foods": 2,  # Wednesday
    "target": 6,       # Sunday
}


# ---------------------------------------------------------------------------#
# Store adapter integration — bridges the async adapter classes into the
# sync scheduler/endpoint context.
# ---------------------------------------------------------------------------

import asyncio

from app.adapters import get_adapter as _get_adapter_class
from app.adapters.base import OfferData


def _run_adapter(store: Store) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Instantiate the store's adapter class, call its async
    ``fetch_current_ad`` and convert the result to a list of plain dicts
    suitable for ``matching.build_offer()``.

    Returns ``(offers, metadata)`` where *offers* is a list of dicts with
    keys matching the ``OfferData`` fields, and *metadata* has keys
    ``period_start``, ``period_end``, ``store_location``, ``raw_payload_ref``.

    On any error returns ``([], {})``.
    """
    try:
        adapter_cls = _get_adapter_class(store.adapter_key)
    except KeyError:
        logger.warning("No adapter registered for '%s'", store.adapter_key)
        return [], {}

    adapter = adapter_cls(store.adapter_key, store.zip_or_store_id)

    async def _fetch() -> tuple[list[OfferData], Any]:
        return await adapter.fetch_current_ad()

    try:
        offers_data, meta = asyncio.run(_fetch())
    except Exception:
        logger.exception("Adapter %s failed for store %s", store.adapter_key, store.name)
        return [], {}

    raw_offers: list[dict[str, Any]] = [o.to_dict() for o in offers_data]
    metadata: dict[str, Any] = {
        "period_start": getattr(meta, "period_start", None),
        "period_end": getattr(meta, "period_end", None),
        "store_location": getattr(meta, "store_location", ""),
        "raw_payload_ref": getattr(meta, "raw_payload_ref", ""),
    }
    return raw_offers, metadata


# ---------------------------------------------------------------------------
# Refresh logic
# ---------------------------------------------------------------------------


def refresh_store(db: Session, store_id: int) -> StoreRefreshResult:
    """Refresh a single store: fetch offers, create ad cycle, run matching.

    Returns a StoreRefreshResult with status and counts.
    """
    store = crud.get_store(db, store_id)
    if store is None:
        return StoreRefreshResult(
            store_id=store_id,
            store_name="unknown",
            status="error",
            error="Store not found",
        )
    if not store.enabled:
        return StoreRefreshResult(
            store_id=store.id,
            store_name=store.name,
            status="skipped",
            error="Store disabled",
        )

    try:
        crud.update_store_status(db, store.id, "fetching")
        raw_offers, adapter_meta = _run_adapter(store)

        # Create or reuse an ad cycle for the current week
        today = date.today()
        period_start = adapter_meta.get("period_start") or today - timedelta(days=today.weekday())
        period_end = adapter_meta.get("period_end") or period_start + timedelta(days=6)

        cycle = AdCycle(
            store_id=store.id,
            period_start=period_start,
            period_end=period_end,
            fetched_at=datetime.utcnow(),
            raw_payload_ref=adapter_meta.get("raw_payload_ref", f"store:{store.adapter_key}:week:{period_start.isoformat()}"),
        )
        db.add(cycle)
        db.commit()
        db.refresh(cycle)

        # Build and save offers
        offer_count = 0
        for raw in raw_offers:
            # Adapters return OfferData dicts with pre-parsed price/deal info.
            # If the adapter already parsed price info, use it directly;
            # otherwise fall back to build_offer which parses from raw_text.
            if raw.get("price", 0) > 0 or raw.get("deal_type", "sale") != "sale":
                offer = Offer(
                    ad_cycle_id=cycle.id,
                    raw_text=raw.get("raw_text", ""),
                    product_name=raw.get("product_name", "") or matching.normalize_offer_text(raw.get("raw_text", "")),
                    brand=raw.get("brand", ""),
                    size_text=raw.get("size_text", ""),
                    price=raw.get("price", 0),
                    deal_type=raw.get("deal_type", "sale"),
                    effective_unit_price=raw.get("effective_unit_price", 0),
                    unit_price_unknown=raw.get("unit_price_unknown", True),
                    requires_membership_or_coupon=raw.get("requires_membership_or_coupon", False),
                )
            else:
                offer = matching.build_offer(
                    ad_cycle_id=cycle.id,
                    raw_text=raw.get("raw_text", ""),
                    product_name=raw.get("product_name", ""),
                    brand=raw.get("brand", ""),
                    size_text=raw.get("size_text", ""),
                )
            db.add(offer)
            offer_count += 1
        db.commit()

        # Run matching pipeline
        match_count = matching.process_matches(db, cycle.id)

        # Update price history
        _update_price_history(db, cycle)

        crud.update_store_status(db, store.id, "success", fetched_at=datetime.utcnow())
        return StoreRefreshResult(
            store_id=store.id,
            store_name=store.name,
            status="success",
            offers_fetched=offer_count,
            matches_created=match_count,
        )

    except Exception as exc:
        logger.exception("Error refreshing store %s", store.name)
        crud.update_store_status(db, store.id, f"error: {exc!s}")
        db.rollback()
        return StoreRefreshResult(
            store_id=store.id,
            store_name=store.name,
            status="error",
            error=str(exc),
        )


def refresh_all_stores() -> RefreshAllResult:
    """Refresh all enabled stores, then compute recommendations + weekly report."""
    db = SessionLocal()
    try:
        stores = crud.get_stores(db)
        results: list[StoreRefreshResult] = []
        total_offers = 0
        total_matches = 0

        for store in stores:
            result = refresh_store(db, store.id)
            results.append(result)
            total_offers += result.offers_fetched
            total_matches += result.matches_created

        # Compute recommendations and weekly report
        weekly_report = recommendations.generate_weekly_report(db)
        report_read = WeeklyReportRead.model_validate(weekly_report)

        return RefreshAllResult(
            results=results,
            total_offers=total_offers,
            total_matches=total_matches,
            weekly_report=report_read,
        )
    finally:
        db.close()


def _update_price_history(db: Session, cycle: AdCycle) -> None:
    """For each item, compute the best unit price at this store for the cycle's week.

    Looks at all confident/accepted matches and picks the lowest effective_unit_price.
    Upserts a PriceHistory row.
    """
    week = cycle.period_start
    store_id = cycle.store_id

    # Get all offers for this cycle
    offers = crud.get_offers_for_cycle(db, cycle.id)
    offer_map = {o.id: o for o in offers}

    # Get all matches for this cycle
    offer_ids = list(offer_map.keys())
    if not offer_ids:
        return

    from app.models import MatchStatus as _MS

    matches = (
        db.query(Match)
        .filter(
            Match.offer_id.in_(offer_ids),
            Match.status.in_([_MS.confident, _MS.accepted]),
        )
        .all()
    )

    # Group best price per item
    best_by_item: dict[int, tuple[int, str]] = {}  # item_id → (price, deal_type)
    for m in matches:
        offer = offer_map.get(m.offer_id)
        if offer is None:
            continue
        unit_price = offer.effective_unit_price if offer.effective_unit_price > 0 else offer.price
        if m.item_id not in best_by_item or unit_price < best_by_item[m.item_id][0]:
            best_by_item[m.item_id] = (unit_price, offer.deal_type)

    for item_id, (price, deal_type) in best_by_item.items():
        crud.upsert_price_history(
            db,
            item_id=item_id,
            store_id=store_id,
            week=week,
            best_unit_price=price,
            deal_type=deal_type,
        )


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------


def _scheduled_refresh_all() -> None:
    """Scheduled job: refresh all stores."""
    logger.info("Scheduled refresh_all_stores starting")
    try:
        result = refresh_all_stores()
        logger.info(
            "Scheduled refresh complete: %d offers, %d matches",
            result.total_offers,
            result.total_matches,
        )
    except Exception:
        logger.exception("Scheduled refresh_all_stores failed")


def _get_schedule_time() -> tuple[int, int]:
    """Read the configured refresh time from settings (HH:MM). Defaults to 07:00."""
    db = SessionLocal()
    try:
        time_str = crud.get_setting(db, "refresh_schedule") or "07:00"
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 7, 0
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    """Start the APScheduler background scheduler with the daily refresh job."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    hour, minute = _get_schedule_time()

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _scheduled_refresh_all,
        CronTrigger(hour=hour, minute=minute),
        id="refresh_all_stores",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started: daily refresh at %02d:%02d", hour, minute)
    return _scheduler


def stop_scheduler() -> None:
    """Shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def reschedule() -> None:
    """Re-apply the schedule from settings (call after settings change)."""
    global _scheduler
    if _scheduler is None:
        return
    hour, minute = _get_schedule_time()
    _scheduler.remove_job("refresh_all_stores")
    _scheduler.add_job(
        _scheduled_refresh_all,
        CronTrigger(hour=hour, minute=minute),
        id="refresh_all_stores",
        replace_existing=True,
    )
    logger.info("Rescheduled: daily refresh at %02d:%02d", hour, minute)
