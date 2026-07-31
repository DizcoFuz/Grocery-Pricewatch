"""APScheduler integration for daily ad refresh.

- Daily refresh at 07:00 local (configurable via settings)
- Per-store refresh on known ad-flip days (Wed for Aldi/Jewel/Mariano's, Sun for Target, etc.)
- refresh_all_stores(): call each enabled store adapter, save offers, run matching,
  update price history, compute recommendations + weekly report
- Per-store status tracking
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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
    logger.info(
        "Adapter %s for store '%s' (zip=%s) returned %d offers",
        store.adapter_key,
        store.name,
        store.zip_or_store_id,
        len(raw_offers),
    )
    if not raw_offers:
        logger.warning(
            "Adapter %s returned 0 offers for store '%s' (zip=%s) — "
            "check network connectivity to the store API",
            store.adapter_key,
            store.name,
            store.zip_or_store_id,
        )
    return raw_offers, metadata


# ---------------------------------------------------------------------------#
# Stale status computation (FR-1.4)
# ---------------------------------------------------------------------------#


def compute_stale_status(
    last_fetch_at: datetime | None,
    last_fetch_status: str | None,
    period_end: date | None = None,
) -> str:
    """Compute the effective store status at read time.

    Rules:
    - If the stored status is ``failed`` or ``partial``, keep it as-is.
    - If the stored status is ``ok`` but ``last_fetch_at`` is older than 24h
      or the ad ``period_end`` is before today, return ``stale``.
    - If no fetch has ever happened, return ``stale``.
    - Otherwise return the stored status.
    """
    if last_fetch_status in ("failed", "partial"):
        return last_fetch_status
    if last_fetch_at is None:
        return "stale"
    if last_fetch_status == "ok":
        now = datetime.now(timezone.utc)
        # Handle both tz-aware and tz-naive datetimes stored in the DB
        fetch_dt = last_fetch_at
        if fetch_dt.tzinfo is None:
            fetch_dt = fetch_dt.replace(tzinfo=timezone.utc)
        if (now - fetch_dt) > timedelta(hours=24):
            return "stale"
        if period_end is not None and period_end < date.today():
            return "stale"
    return last_fetch_status or "stale"


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
            status="failed",
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

        # Upsert the AdCycle keyed on (store_id, period_start) so daily
        # refreshes of the same ad week replace offers atomically instead of
        # creating duplicate cycles (P1-3).
        existing_cycle = (
            db.query(AdCycle)
            .filter(
                AdCycle.store_id == store.id,
                AdCycle.period_start == period_start,
            )
            .first()
        )
        if existing_cycle is not None:
            # Reuse the cycle; delete its existing offers (matches cascade).
            db.query(Offer).filter(Offer.ad_cycle_id == existing_cycle.id).delete(
                synchronize_session="fetch"
            )
            existing_cycle.period_end = period_end
            existing_cycle.fetched_at = datetime.now(timezone.utc)
            existing_cycle.raw_payload_ref = adapter_meta.get(
                "raw_payload_ref",
                f"store:{store.adapter_key}:week:{period_start.isoformat()}",
            )
            db.commit()
            cycle = existing_cycle
        else:
            cycle = AdCycle(
                store_id=store.id,
                period_start=period_start,
                period_end=period_end,
                fetched_at=datetime.now(timezone.utc),
                raw_payload_ref=adapter_meta.get(
                    "raw_payload_ref",
                    f"store:{store.adapter_key}:week:{period_start.isoformat()}",
                ),
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
                # Adapter already parsed price info.  Run it through
                # normalize_price too so the new per-item / per-oz bases get
                # populated consistently (P0-4).  We keep the adapter's values
                # as fallbacks for effective_unit_price when normalization
                # can't improve on them.
                np = matching.normalize_price(
                    raw.get("raw_text", ""), size_text=raw.get("size_text", "")
                )
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
                    # Prefer the normalized bases; fall back to raw adapter
                    # values only if normalize_price returned None.
                    price_per_item_cents=np.price_per_item_cents if np.price_per_item_cents is not None else None,
                    price_per_oz_cents=np.price_per_oz_cents if np.price_per_oz_cents is not None else None,
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

        logger.info(
            "Store '%s': saved %d offers to cycle %d",
            store.name, offer_count, cycle.id,
        )

        # Run matching pipeline
        match_count = matching.process_matches(db, cycle.id)
        logger.info(
            "Store '%s': process_matches created %d matches",
            store.name, match_count,
        )

        # Update price history
        _update_price_history(db, cycle)

        # Determine final status: "partial" if the adapter signaled partial OCR,
        # otherwise "ok" (W-3 from fifth review).
        final_status = "ok"
        if adapter_meta and getattr(adapter_meta, "raw_payload_ref", "") == "partial":
            final_status = "partial"

        crud.update_store_status(db, store.id, final_status, fetched_at=datetime.now(timezone.utc))
        return StoreRefreshResult(
            store_id=store.id,
            store_name=store.name,
            status=final_status,
            offers_fetched=offer_count,
            matches_created=match_count,
        )

    except Exception as exc:
        logger.exception("Error refreshing store %s", store.name)
        crud.update_store_status(db, store.id, "failed")
        db.rollback()
        return StoreRefreshResult(
            store_id=store.id,
            store_name=store.name,
            status="failed",
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
    item_cache: dict[int, Item] = {}
    for m in matches:
        offer = offer_map.get(m.offer_id)
        if offer is None:
            continue
        item = item_cache.get(m.item_id)
        if item is None:
            item = db.get(Item, m.item_id)
            if item is not None:
                item_cache[m.item_id] = item
        if item is None:
            continue
        # P0-4: use comparable price in the item's UoM basis.
        unit_price, _approx = matching.compute_comparable_price(offer, item)
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


# ---------------------------------------------------------------------------#
# Scheduler setup
# ---------------------------------------------------------------------------#


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


def _scheduled_refresh_store(store_id: int) -> None:
    """Scheduled job: refresh a single store (used for ad-flip-day jobs)."""
    logger.info("Scheduled refresh_store starting for store_id=%s", store_id)
    db = SessionLocal()
    try:
        result = refresh_store(db, store_id)
        logger.info(
            "Scheduled refresh_store complete: store=%s status=%s offers=%d matches=%d",
            result.store_name,
            result.status,
            result.offers_fetched,
            result.matches_created,
        )
    except Exception:
        logger.exception("Scheduled refresh_store failed for store_id=%s", store_id)
    finally:
        db.close()


def _cleanup_old_payloads(retention_days: int = 90) -> None:
    """Delete raw payload files older than *retention_days* (P1-7).

    Scans the raw_payloads directory (resolved via the StoreAdapter helper, or
    /data/raw_payloads) and deletes files whose modification time is older than
    the retention window.  Logs the number of files deleted.
    """
    from app.adapters.base import StoreAdapter

    try:
        payload_dir = StoreAdapter._resolve_data_dir()
    except Exception:
        payload_dir = Path("/data/raw_payloads")
        payload_dir.mkdir(parents=True, exist_ok=True)

    cutoff = time.time() - retention_days * 86400
    deleted = 0
    try:
        for entry in payload_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                try:
                    entry.unlink()
                    deleted += 1
                except OSError as exc:
                    logger.warning("Could not delete old payload %s: %s", entry, exc)
    except OSError as exc:
        logger.warning("Could not scan payload dir %s: %s", payload_dir, exc)

    if deleted:
        logger.info("Cleanup: deleted %d raw payload files older than %d days", deleted, retention_days)
    else:
        logger.debug("Cleanup: no raw payload files older than %d days", retention_days)


def _scheduled_cleanup_payloads() -> None:
    """Scheduled job: delete raw payloads older than 90 days."""
    try:
        _cleanup_old_payloads(retention_days=90)
    except Exception:
        logger.exception("Scheduled payload cleanup failed")


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


def _get_schedule_timezone() -> str:
    """Return the timezone for scheduling: from TZ env var or system default."""
    tz = os.environ.get("TZ")
    if tz:
        return tz
    # Fall back to the system local timezone, or UTC if not detectable.
    try:
        import datetime as _dt
        localname = _dt.datetime.now(_dt.timezone.utc).astimezone().tzname()
        if localname:
            return localname
    except Exception:
        pass
    return "UTC"


def start_scheduler() -> BackgroundScheduler:
    """Start the APScheduler background scheduler.

    Registers:
    - The daily refresh-all job at the configured time (HH:MM from settings).
    - Per-store ad-flip-day jobs that refresh an individual store on the
      weekday its weekly ad flips (P1-5).
    - A daily raw-payload retention cleanup job (P1-7).
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    hour, minute = _get_schedule_time()
    tz = _get_schedule_timezone()

    _scheduler = BackgroundScheduler(timezone=tz)

    # Daily refresh-all job
    _scheduler.add_job(
        _scheduled_refresh_all,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="refresh_all_stores",
        replace_existing=True,
    )

    # Per-store ad-flip-day jobs (P1-5)
    db = SessionLocal()
    try:
        stores = crud.get_stores(db)
        for store in stores:
            flip_day = AD_FLIP_DAYS.get(store.adapter_key)
            if flip_day is None:
                continue
            job_id = f"refresh_store_flip_{store.id}"
            _scheduler.add_job(
                _scheduled_refresh_store,
                CronTrigger(
                    day_of_week=flip_day,
                    hour=hour,
                    minute=minute,
                    timezone=tz,
                ),
                args=[store.id],
                id=job_id,
                replace_existing=True,
            )
            logger.info(
                "Ad-flip-day job registered: store=%s day_of_week=%d at %02d:%02d %s",
                store.adapter_key,
                flip_day,
                hour,
                minute,
                tz,
            )
    finally:
        db.close()

    # Daily 90-day raw-payload retention cleanup (P1-7), 1 hour after refresh
    cleanup_hour = hour + 1
    if cleanup_hour >= 24:
        cleanup_hour = 0
    _scheduler.add_job(
        _scheduled_cleanup_payloads,
        CronTrigger(hour=cleanup_hour, minute=minute, timezone=tz),
        id="cleanup_old_payloads",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "Scheduler started: daily refresh at %02d:%02d %s, payload cleanup at %02d:%02d",
        hour,
        minute,
        tz,
        cleanup_hour,
        minute,
    )
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
    # The simplest correct approach: stop and restart, which re-reads settings
    # and re-registers all jobs (daily, ad-flip-day, cleanup).
    _scheduler.shutdown(wait=False)
    _scheduler = None
    start_scheduler()
