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
from datetime import date
from itertools import combinations

from sqlalchemy.orm import Session

from app import crud
from app.models import AdCycle, Item, Match, MatchStatus, Offer, Store
from app.schemas import (
    RecommendationsResponse,
    StoreCostDetail,
    SingleStoreRecommendation,
    TwoStoreRecommendation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_item_baseline(item: Item) -> int:
    """Return baseline unit price in cents.

    Uses item.baseline_price_override if set, else a heuristic default.
    """
    if item.baseline_price_override is not None:
        return item.baseline_price_override
    # Sensible default per-category could be added; for now use $5.00 (500c)
    return 500


def _get_latest_offers_for_store(db: Session, store_id: int) -> list[Offer]:
    """Return all offers from the most recent ad cycle for a store."""
    cycle = crud.get_latest_ad_cycle(db, store_id)
    if cycle is None:
        return []
    return crud.get_offers_for_cycle(db, cycle.id)


def _get_best_match_for_item(
    db: Session, item_id: int, store_id: int
) -> tuple[Match | None, Offer | None]:
    """Find the best (lowest effective_unit_price) accepted/confident match
    for an item at a given store.
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
            Match.item_id == item_id,
            Match.status.in_([MatchStatus.confident, MatchStatus.accepted]),
        )
        .all()
    )
    if not matches:
        return None, None
    # Pick the match whose offer has the lowest effective_unit_price
    best_match: Match | None = None
    best_offer: Offer | None = None
    best_price = None
    for m in matches:
        offer = db.get(Offer, m.offer_id)
        if offer is None:
            continue
        price = offer.effective_unit_price if offer.effective_unit_price > 0 else offer.price
        if best_price is None or price < best_price:
            best_match = m
            best_offer = offer
            best_price = price
    return best_match, best_offer


def _get_best_match_uncertain(
    db: Session, item_id: int, store_id: int
) -> tuple[Match | None, Offer | None]:
    """Find uncertain (potential) match for an item at a store."""
    cycle = crud.get_latest_ad_cycle(db, store_id)
    if cycle is None:
        return None, None
    matches = (
        db.query(Match)
        .join(Offer, Match.offer_id == Offer.id)
        .filter(
            Offer.ad_cycle_id == cycle.id,
            Match.item_id == item_id,
            Match.status == MatchStatus.uncertain,
        )
        .all()
    )
    if not matches:
        return None, None
    best_match = matches[0]
    best_offer = db.get(Offer, best_match.offer_id)
    return best_match, best_offer


# ---------------------------------------------------------------------------
# Single store
# ---------------------------------------------------------------------------


def single_store_recommendation(db: Session) -> list[SingleStoreRecommendation]:
    """Compute the basket cost at each store.

    For each active item, use the sale price if there is a confident/accepted match
    at that store; otherwise use the baseline unit price.
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
            baseline = _get_item_baseline(item)
            baseline_line = int(round(baseline * item.typical_quantity))
            baseline_cost += baseline_line

            match, offer = _get_best_match_for_item(db, item.id, store.id)
            if match and offer:
                unit_price = offer.effective_unit_price if offer.effective_unit_price > 0 else offer.price
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
            else:
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
            baseline = _get_item_baseline(item)
            match, offer = _get_best_match_for_item(db, item.id, store.id)
            if match and offer:
                unit_price = offer.effective_unit_price if offer.effective_unit_price > 0 else offer.price
                line_total = int(round(unit_price * item.typical_quantity))
                store_map[item.id] = (unit_price, True, offer.deal_type, line_total)
            else:
                line_total = int(round(baseline * item.typical_quantity))
                store_map[item.id] = (baseline, False, "baseline", line_total)
        single_maps[store.id] = store_map

    # Baseline total
    baseline_cost = sum(
        int(round(_get_item_baseline(item) * item.typical_quantity)) for item in items
    )

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
                # Should not happen since we computed for all items, but guard
                baseline = _get_item_baseline(item)
                line_total = int(round(baseline * item.typical_quantity))
                total_cost += line_total
                details.append(
                    StoreCostDetail(
                        item_id=item.id,
                        item_name=item.name,
                        unit_price=baseline,
                        quantity=item.typical_quantity,
                        line_total=line_total,
                        is_sale=False,
                        deal_type="baseline",
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

    return RecommendationsResponse(
        single=single,
        best_single=best_single,
        two_store=two_store,
        best_pair=best_pair,
        two_store_threshold=threshold,
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
