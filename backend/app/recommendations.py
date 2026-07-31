"""Store recommendation engine.

- single_store_recommendation: for each store, sum basket cost weighted by typical_quantity.
  Sale price if a confident/accepted match exists; else baseline.
- two_store_recommendation: brute-force all store pairs; for each item pick cheaper of the two.
- Marginal benefit of pair vs best single (threshold from settings, default $5 = 500 cents).
- Only count confident/accepted matches; uncertain shown as potential savings.

All price math in integer cents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations

from sqlalchemy.orm import Session

from app import crud
from app.matching import compute_comparable_price
from app.models import AdCycle, Item, Match, MatchStatus, Offer, PriceHistory, Store
from app.schemas import (
    BestPriceEntry,
    BestPricesResponse,
    RecommendationsResponse,
    StoreCostDetail,
    SingleStoreRecommendation,
    TwoStoreRecommendation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Deal types that represent a sale (excluded from the non-sale baseline window).
_SALE_DEAL_TYPES = frozenset(
    {"sale", "multi_buy", "bogo", "rollback", "circle", "prime", "per_lb", "2_for"}
)


def _get_item_baseline(db: Session, item: Item) -> int | None:
    """3-tier baseline (P0-2): user override → rolling median of non-sale
    PriceHistory (last 12 weeks) → None.

    Returns None when no baseline is derivable; callers must skip the item
    from savings totals in that case (do NOT invent $5.00).
    """
    # Tier 1: user override
    if item.baseline_price_override is not None and item.baseline_price_override > 0:
        return item.baseline_price_override

    # Honor the baseline_strategy setting: "manual" = override-only (no history fallback).
    settings = crud.get_all_settings(db)
    strategy = (settings.get("baseline_strategy") or "auto").strip().lower()
    if strategy == "manual":
        return None

    # Tier 2: rolling median of observed non-sale prices from PriceHistory.
    cutoff = date.today() - timedelta(weeks=12)
    history = (
        db.query(PriceHistory)
        .filter(
            PriceHistory.item_id == item.id,
            PriceHistory.week >= cutoff,
        )
        .all()
    )
    # Non-sale observations: deal_type not in the sale set.
    non_sale = [
        h.best_unit_price
        for h in history
        if (h.deal_type or "").strip().lower() not in _SALE_DEAL_TYPES
        and h.best_unit_price > 0
    ]
    if non_sale:
        non_sale.sort()
        mid = len(non_sale) // 2
        if len(non_sale) % 2 == 0:
            return (non_sale[mid - 1] + non_sale[mid]) // 2
        return non_sale[mid]

    # Tier 3: no derivable baseline.
    return None


def _get_latest_offers_for_store(db: Session, store_id: int) -> list[Offer]:
    """Return all offers from the most recent ad cycle for a store."""
    cycle = crud.get_latest_ad_cycle(db, store_id)
    if cycle is None:
        return []
    return crud.get_offers_for_cycle(db, cycle.id)


def _get_best_match_for_item(
    db: Session, item: Item, store_id: int
) -> tuple[Match | None, Offer | None]:
    """Find the best (lowest comparable price) accepted/confident match
    for an item at a given store (P0-4: uses compute_comparable_price).
    """
    # Find the latest ad cycle for this store
    cycle = crud.get_latest_ad_cycle(db, store_id)
    if cycle is None:
        return None, None
    # Query matches for this item in this cycle's offers, confident or accepted
    matches = (
        db.query(Match)
        .join(Offer, Match.offer_id == Offer.id)
        .filter(
            Offer.ad_cycle_id == cycle.id,
            Match.item_id == item.id,
            Match.status.in_([MatchStatus.confident, MatchStatus.accepted]),
        )
        .all()
    )
    if not matches:
        return None, None
    # Pick the match whose offer has the lowest comparable price.
    # P0-4: precise (non-approximate) offers beat approximate ones at equal price.
    best_match: Match | None = None
    best_offer: Offer | None = None
    best_price: int | None = None
    best_approx: bool = True
    for m in matches:
        offer = db.get(Offer, m.offer_id)
        if offer is None:
            continue
        price, approx = compute_comparable_price(offer, item)
        if best_price is None:
            best_match, best_offer, best_price, best_approx = m, offer, price, approx
            continue
        # A precise offer wins ties against an approximate one.
        if price < best_price or (price == best_price and best_approx and not approx):
            best_match, best_offer, best_price, best_approx = m, offer, price, approx
    return best_match, best_offer


def _get_best_match_uncertain(
    db: Session, item: Item, store_id: int
) -> tuple[Match | None, Offer | None]:
    """Find the lowest-priced uncertain (potential) match for an item at a
    store (same pattern as _get_best_match_for_item but for uncertain status).
    """
    cycle = crud.get_latest_ad_cycle(db, store_id)
    if cycle is None:
        return None, None
    matches = (
        db.query(Match)
        .join(Offer, Match.offer_id == Offer.id)
        .filter(
            Offer.ad_cycle_id == cycle.id,
            Match.item_id == item.id,
            Match.status == MatchStatus.uncertain,
        )
        .all()
    )
    if not matches:
        return None, None
    # Pick the match whose offer has the lowest comparable price.
    # P0-4: precise (non-approximate) offers beat approximate ones at equal price.
    best_match: Match | None = None
    best_offer: Offer | None = None
    best_price: int | None = None
    best_approx: bool = True
    for m in matches:
        offer = db.get(Offer, m.offer_id)
        if offer is None:
            continue
        price, approx = compute_comparable_price(offer, item)
        if best_price is None:
            best_match, best_offer, best_price, best_approx = m, offer, price, approx
            continue
        # A precise offer wins ties against an approximate one.
        if price < best_price or (price == best_price and best_approx and not approx):
            best_match, best_offer, best_price, best_approx = m, offer, price, approx
    return best_match, best_offer


# ---------------------------------------------------------------------------
# Single store
# ---------------------------------------------------------------------------


def single_store_recommendation(db: Session) -> list[SingleStoreRecommendation]:
    """Compute the basket cost at each store.

    For each active item, use the sale price if there is a confident/accepted match
    at that store; otherwise use the baseline unit price.  Items with no
    derivable baseline (P0-2) are excluded from savings totals.
    Total is weighted by typical_quantity.
    Returns a sorted list (lowest cost first).
    """
    items = crud.get_items(db, active_only=True)
    stores = crud.get_stores(db)

    results: list[SingleStoreRecommendation] = []

    for store in stores:
        if not store.enabled:
            continue
        details: list[StoreCostDetail] = []
        total_cost = 0
        baseline_cost = 0

        for item in items:
            baseline = _get_item_baseline(db, item)
            has_baseline = baseline is not None
            baseline_line = (
                int(round(baseline * item.typical_quantity)) if has_baseline else 0
            )
            # Only items with a baseline contribute to baseline_cost (P0-2).
            if has_baseline:
                baseline_cost += baseline_line

            match, offer = _get_best_match_for_item(db, item, store.id)
            if match and offer:
                # P0-4: comparable price in the item's UoM basis.
                unit_price, _approx = compute_comparable_price(offer, item)
                line_total = int(round(unit_price * item.typical_quantity))
                total_cost += line_total
                details.append(
                    StoreCostDetail(
                        item_id=item.id,
                        item_name=item.name,
                        unit_price=unit_price,
                        quantity=item.typical_quantity,
                        line_total=line_total,
                        is_sale=True,
                        deal_type=offer.deal_type,
                    )
                )
            elif has_baseline:
                total_cost += baseline_line
                details.append(
                    StoreCostDetail(
                        item_id=item.id,
                        item_name=item.name,
                        unit_price=baseline,
                        quantity=item.typical_quantity,
                        line_total=baseline_line,
                        is_sale=False,
                        deal_type="baseline",
                    )
                )
            else:
                # No sale and no baseline: skip from totals but list as
                # no-baseline so the UI can show "no baseline".
                details.append(
                    StoreCostDetail(
                        item_id=item.id,
                        item_name=item.name,
                        unit_price=0,
                        quantity=item.typical_quantity,
                        line_total=0,
                        is_sale=False,
                        deal_type="no_baseline",
                    )
                )

        savings = max(baseline_cost - total_cost, 0)
        results.append(
            SingleStoreRecommendation(
                store_id=store.id,
                store_name=store.name,
                total_cost=total_cost,
                baseline_cost=baseline_cost,
                savings=savings,
                item_count=len(items),
                details=details,
            )
        )

    results.sort(key=lambda r: r.total_cost)
    return results


# ---------------------------------------------------------------------------
# Two store
# ---------------------------------------------------------------------------


def two_store_recommendation(
    db: Session, *, two_store_threshold: int = 500
) -> list[TwoStoreRecommendation]:
    """Brute-force all store pairs.

    For each pair, for each item pick the cheaper of the two stores
    (sale price if matched, else baseline). Compute total cost and savings.
    Also compute marginal benefit vs the best single store.
    """
    items = crud.get_items(db, active_only=True)
    stores = [s for s in crud.get_stores(db) if s.enabled]

    # Precompute single-store cost maps (item_id → (unit_price, is_sale, deal_type, line_total))
    single_maps: dict[int, dict[int, tuple[int, bool, str, int]]] = {}
    for store in stores:
        store_map: dict[int, tuple[int, bool, str, int]] = {}
        for item in items:
            baseline = _get_item_baseline(db, item)
            has_baseline = baseline is not None
            match, offer = _get_best_match_for_item(db, item, store.id)
            if match and offer:
                # P0-4: comparable price in the item's UoM basis.
                unit_price, _approx = compute_comparable_price(offer, item)
                line_total = int(round(unit_price * item.typical_quantity))
                store_map[item.id] = (unit_price, True, offer.deal_type, line_total)
            elif has_baseline:
                line_total = int(round(baseline * item.typical_quantity))
                store_map[item.id] = (baseline, False, "baseline", line_total)
            else:
                # No sale, no baseline → contribute nothing to totals.
                store_map[item.id] = (0, False, "no_baseline", 0)
        single_maps[store.id] = store_map

    # Baseline total — only items with a derivable baseline contribute (P0-2).
    baseline_cost = 0
    for item in items:
        b = _get_item_baseline(db, item)
        if b is not None:
            baseline_cost += int(round(b * item.typical_quantity))

    # Best single store total (for marginal benefit)
    single_results = single_store_recommendation(db)
    best_single_total = min((r.total_cost for r in single_results), default=0)

    results: list[TwoStoreRecommendation] = []

    for store_a, store_b in combinations(stores, 2):
        details: list[StoreCostDetail] = []
        item_store_map: dict[int, int] = {}
        total_cost = 0

        for item in items:
            map_a = single_maps[store_a.id].get(item.id)
            map_b = single_maps[store_b.id].get(item.id)
            if map_a is None or map_b is None:
                # Should not happen since we computed for all items, but guard.
                # P0-2: skip items with no baseline from totals.
                b = _get_item_baseline(db, item)
                if b is not None:
                    line_total = int(round(b * item.typical_quantity))
                    total_cost += line_total
                    details.append(
                        StoreCostDetail(
                            item_id=item.id,
                            item_name=item.name,
                            unit_price=b,
                            quantity=item.typical_quantity,
                            line_total=line_total,
                            is_sale=False,
                            deal_type="baseline",
                        )
                    )
                else:
                    details.append(
                        StoreCostDetail(
                            item_id=item.id,
                            item_name=item.name,
                            unit_price=0,
                            quantity=item.typical_quantity,
                            line_total=0,
                            is_sale=False,
                            deal_type="no_baseline",
                        )
                    )
                continue

            # Pick the cheaper store for this item
            if map_a[3] <= map_b[3]:
                chosen_store = store_a.id
                unit_price, is_sale, deal_type, line_total = map_a
            else:
                chosen_store = store_b.id
                unit_price, is_sale, deal_type, line_total = map_b

            total_cost += line_total
            item_store_map[item.id] = chosen_store
            details.append(
                StoreCostDetail(
                    item_id=item.id,
                    item_name=item.name,
                    unit_price=unit_price,
                    quantity=item.typical_quantity,
                    line_total=line_total,
                    is_sale=is_sale,
                    deal_type=deal_type,
                )
            )

        savings = max(baseline_cost - total_cost, 0)
        marginal = max(best_single_total - total_cost, 0)

        results.append(
            TwoStoreRecommendation(
                store_ids=[store_a.id, store_b.id],
                store_names=[store_a.name, store_b.name],
                total_cost=total_cost,
                baseline_cost=baseline_cost,
                savings=savings,
                marginal_benefit=marginal,
                item_count=len(items),
                details=details,
                item_store_map=item_store_map,
            )
        )

    # Sort by total cost (lowest first)
    results.sort(key=lambda r: r.total_cost)
    return results


# ---------------------------------------------------------------------------
# Combined response
# ---------------------------------------------------------------------------


def compute_recommendations(db: Session) -> RecommendationsResponse:
    """Compute all recommendations and return the combined response."""
    settings = crud.get_all_settings(db)
    threshold = int(settings.get("two_store_threshold", "500"))

    single = single_store_recommendation(db)
    best_single = single[0] if single else None

    two_store = two_store_recommendation(db, two_store_threshold=threshold)
    # Only recommend the pair if marginal benefit >= threshold
    best_pair: TwoStoreRecommendation | None = None
    for pair in two_store:
        if pair.marginal_benefit >= threshold:
            best_pair = pair
            break
    if best_pair is None and two_store:
        # Even if below threshold, show the best pair but note it's not recommended
        best_pair = two_store[0]

    # FR-5.4: potential additional savings pending review.
    # For each item that has uncertain matches but no confident/accepted match
    # at the best store, compute the potential savings (baseline - uncertain_price)
    # and sum them.
    potential_savings_pending_review = 0
    if best_single is not None:
        items = crud.get_items(db, active_only=True)
        best_store_id = best_single.store_id
        for item in items:
            baseline = _get_item_baseline(db, item)
            if baseline is None:
                continue
            # Check if there's a confident/accepted match at the best store
            confident_match, confident_offer = _get_best_match_for_item(db, item, best_store_id)
            if confident_match is not None:
                continue  # Already has a confident/accepted match — no pending savings
            # Check for uncertain matches at the best store
            uncertain_match, uncertain_offer = _get_best_match_uncertain(db, item, best_store_id)
            if uncertain_match is not None and uncertain_offer is not None:
                uncertain_price, _ = compute_comparable_price(uncertain_offer, item)
                potential = baseline - uncertain_price
                if potential > 0:
                    potential_savings_pending_review += potential

    return RecommendationsResponse(
        single=single,
        best_single=best_single,
        two_store=two_store,
        best_pair=best_pair,
        two_store_threshold=threshold,
        potential_savings_pending_review=potential_savings_pending_review,
    )


# ---------------------------------------------------------------------------
# Weekly report generation
# ---------------------------------------------------------------------------


def generate_weekly_report(db: Session, week: date | None = None) -> object:
    """Compute and persist the weekly report for the given (or current) week."""
    if week is None:
        week = date.today()

    recs = compute_recommendations(db)

    best_single_store_id = recs.best_single.store_id if recs.best_single else None
    best_pair_store_ids = recs.best_pair.store_ids if recs.best_pair else []
    savings_single = recs.best_single.savings if recs.best_single else 0
    savings_pair = recs.best_pair.savings if recs.best_pair else 0

    per_item = []
    if recs.best_single:
        for d in recs.best_single.details:
            per_item.append(
                {
                    "item_id": d.item_id,
                    "item_name": d.item_name,
                    "unit_price": d.unit_price,
                    "line_total": d.line_total,
                    "is_sale": d.is_sale,
                    "deal_type": d.deal_type,
                }
            )

    report = crud.upsert_weekly_report(
        db,
        week=week,
        best_single_store_id=best_single_store_id,
        best_pair_store_ids=best_pair_store_ids,
        projected_savings_single=savings_single,
        projected_savings_pair=savings_pair,
        per_item_results_json=json.dumps(per_item),
    )
    return report


# ---------------------------------------------------------------------------
# Best prices — current vs. last best (P0-3, headline feature FR-4.2/4.3)
# ---------------------------------------------------------------------------


def _store_name(db: Session, store_id: int | None) -> str:
    if store_id is None:
        return ""
    s = db.get(Store, store_id)
    return s.name if s else ""


def compute_best_prices(db: Session) -> BestPricesResponse:
    """Per active item: current best offer/store, last best from PriceHistory
    (excluding the current week), all-time min, delta + direction.

    FR-4.2: "last best" = most recent PriceHistory row *excluding* the current
    week, skipping weeks with no matches.  "all-time best" = the global min.
    FR-4.3: items with no confident/accepted matches this week go in the
    "without deals" list.
    """
    items = crud.get_items(db, active_only=True)
    stores = [s for s in crud.get_stores(db) if s.enabled]

    with_deals: list[BestPriceEntry] = []
    without_deals: list[BestPriceEntry] = []

    today = date.today()

    for item in items:
        # 1. Find all confident/accepted matches in current offers across ALL stores.
        # Look at ALL cycles whose period_start is today (the current ad week),
        # not just the latest cycle per store — this handles the case where
        # multiple cycles exist for the same store in the same week (which
        # the WP-D cycle upsert prevents in production but tests may create).
        current_candidates: list[tuple[int, int, str, Offer, Store, bool]] = []
        # (price, store_id, deal_type, offer, store, approximate)
        enabled_store_ids = {s.id for s in stores}
        # Look at cycles whose period_end >= today (still valid/current)
        today_cycles = (
            db.query(AdCycle)
            .filter(
                AdCycle.period_end >= today,
                AdCycle.store_id.in_(enabled_store_ids),
            )
            .all()
        )
        for cycle in today_cycles:
            matches = (
                db.query(Match)
                .join(Offer, Match.offer_id == Offer.id)
                .filter(
                    Offer.ad_cycle_id == cycle.id,
                    Match.item_id == item.id,
                    Match.status.in_([
                        MatchStatus.confident,
                        MatchStatus.accepted,
                        MatchStatus.uncertain,
                    ]),
                )
                .all()
            )
            for m in matches:
                offer = db.get(Offer, m.offer_id)
                if offer is None:
                    continue
                store = db.get(Store, cycle.store_id)
                if store is None:
                    continue
                price, approx = compute_comparable_price(offer, item)
                current_candidates.append(
                    (price, store.id, offer.deal_type, offer, store, approx)
                )

        # 2. Query PriceHistory for this item (all weeks).
        history = (
            db.query(PriceHistory)
            .filter(PriceHistory.item_id == item.id)
            .order_by(PriceHistory.week.desc())
            .all()
        )

        if not current_candidates:
            # No deals this week → "without deals" list (FR-4.3).
            # Still surface last_best + all_time_best if known.
            entry = BestPriceEntry(
                item_id=item.id,
                item_name=item.name,
                category=item.category,
                current_best_price=None,
                current_best_store_name="",
                current_best_deal_type="",
            )
            _populate_history_fields(db, entry, history, today)
            without_deals.append(entry)
            continue

        # Pick the lowest comparable price → current_best.
        # P0-4: precise offers beat approximate ones at equal price.
        current_candidates.sort(key=lambda c: (c[0], 1 if c[5] else 0))
        best_price, best_store_id, best_deal_type, best_offer, best_store, best_approx = (
            current_candidates[0]
        )

        # Collect other stores' prices for the expandable row (FR-4.2).
        other_store_prices: list[dict] = []
        seen_stores: set[int] = {best_store_id}
        for price, sid, dtype, offer, store, approx in current_candidates[1:]:
            if sid in seen_stores:
                continue
            seen_stores.add(sid)
            other_store_prices.append(
                {
                    "store_name": store.name,
                    "price": price,
                    "deal_type": dtype,
                    "unit_price_unknown": approx,
                }
            )

        entry = BestPriceEntry(
            item_id=item.id,
            item_name=item.name,
            category=item.category,
            current_best_price=best_price,
            current_best_store_id=best_store_id,
            current_best_store_name=best_store.name,
            current_best_deal_type=best_deal_type,
            unit_price_unknown=best_approx,
            other_store_prices=other_store_prices,
        )

        # 3 & 4. last_best / all_time_best from PriceHistory (exclude current week).
        _populate_history_fields(db, entry, history, today, current_best_price=best_price)

        with_deals.append(entry)

    # Sort: deals by biggest improvement (most negative delta) first, then price;
    # without-deals alphabetically.
    with_deals.sort(key=lambda e: (e.delta_cents if e.delta_cents is not None else 0, e.current_best_price or 0))
    without_deals.sort(key=lambda e: e.item_name.lower())

    return BestPricesResponse(
        items_with_deals=with_deals,
        items_without_deals=without_deals,
    )


def _populate_history_fields(
    db: Session,
    entry: BestPriceEntry,
    history: list[PriceHistory],
    today: date,
    *,
    current_best_price: int | None = None,
) -> None:
    """Fill last_best / all_time_best / delta on *entry* from *history*.

    "Last best" (FR-4.2) = the most recent PriceHistory row whose week is
    strictly before the current week, skipping weeks with no matches.
    """
    if not history:
        if current_best_price is not None:
            entry.last_best_price = None
            entry.delta_cents = None
            entry.delta_direction = "new"
        return

    # All-time best = min best_unit_price across all history.
    priced = [h for h in history if h.best_unit_price and h.best_unit_price > 0]
    if priced:
        all_time = min(priced, key=lambda h: h.best_unit_price)
        entry.all_time_best_price = all_time.best_unit_price
        entry.all_time_best_store_name = _store_name(db, all_time.store_id)
        entry.all_time_best_week = all_time.week.isoformat() if all_time.week else ""

    # Last best = most recent history row strictly before the current week.
    # "Current week" is today's week — the current ad cycle, not the
    # most recent PriceHistory row (which may not exist yet for this week).
    prior = [h for h in history if h.week < today and h.best_unit_price > 0]
    if not prior:
        # No prior history → this is the first observation.
        if current_best_price is not None:
            entry.last_best_price = None
            entry.delta_cents = None
            entry.delta_direction = "new"
        return

    last = prior[0]  # history is ordered desc by week
    entry.last_best_price = last.best_unit_price
    entry.last_best_store_name = _store_name(db, last.store_id)
    entry.last_best_week = last.week.isoformat() if last.week else ""

    if current_best_price is not None and entry.last_best_price is not None:
        delta = current_best_price - entry.last_best_price
        entry.delta_cents = delta
        if delta < 0:
            entry.delta_direction = "better"
        elif delta > 0:
            entry.delta_direction = "worse"
        else:
            entry.delta_direction = "unchanged"
    elif current_best_price is not None:
        entry.delta_direction = "new"
