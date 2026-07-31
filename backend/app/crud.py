"""CRUD operations for all models.

Includes:
- Store / Item / Offer / Match / PriceHistory / WeeklyReport / Setting CRUD
- Item import from CSV/JSON with dedup + preview + per-row errors
- Item export to CSV/JSON
- Template generation
- Match review (accept/reject)
- Price history retrieval
- Settings get/set
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    AdCycle,
    Item,
    Match,
    MatchDecidedBy,
    MatchRule,
    MatchStatus,
    Offer,
    PriceHistory,
    Setting,
    Store,
    WeeklyReport,
)
from app.schemas import (
    ItemCreate,
    ItemImportResult,
    ItemRead,
    ItemUpdate,
    StoreCreate,
    StoreUpdate,
)


# ============================================================================
# Store
# ============================================================================


def get_stores(db: Session) -> list[Store]:
    return list(db.query(Store).order_by(Store.name).all())


def get_store(db: Session, store_id: int) -> Store | None:
    return db.get(Store, store_id)


def get_store_by_adapter_key(db: Session, adapter_key: str) -> Store | None:
    return db.query(Store).filter(Store.adapter_key == adapter_key).first()


def create_store(db: Session, store_in: StoreCreate) -> Store:
    store = Store(**store_in.model_dump())
    db.add(store)
    db.commit()
    db.refresh(store)
    return store


def update_store(db: Session, store_id: int, store_in: StoreUpdate) -> Store | None:
    store = db.get(Store, store_id)
    if store is None:
        return None
    data = store_in.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(store, k, v)
    db.commit()
    db.refresh(store)
    return store


def delete_store(db: Session, store_id: int) -> bool:
    store = db.get(Store, store_id)
    if store is None:
        return False
    db.delete(store)
    db.commit()
    return True


def update_store_status(
    db: Session, store_id: int, status: str, fetched_at: datetime | None = None
) -> None:
    store = db.get(Store, store_id)
    if store is None:
        return
    store.last_fetch_status = status
    store.last_fetch_at = fetched_at or datetime.now(timezone.utc)
    db.commit()


# ============================================================================
# Item
# ============================================================================


def get_items(db: Session, active_only: bool = False) -> list[Item]:
    q = db.query(Item)
    if active_only:
        q = q.filter(Item.active.is_(True))
    return list(q.order_by(Item.name).all())


def get_item(db: Session, item_id: int) -> Item | None:
    return db.get(Item, item_id)


def find_item_by_name(db: Session, name: str) -> Item | None:
    """Case-insensitive name lookup for dedup."""
    return (
        db.query(Item)
        .filter(Item.name.ilike(name.strip()))
        .first()
    )


def create_item(db: Session, item_in: ItemCreate) -> Item:
    item = Item(**item_in.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_item(db: Session, item_id: int, item_in: ItemUpdate) -> Item | None:
    item = db.get(Item, item_id)
    if item is None:
        return None
    data = item_in.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


def delete_item(db: Session, item_id: int) -> bool:
    item = db.get(Item, item_id)
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


# ============================================================================
# Item Import / Export / Template
# ============================================================================


def _parse_list_field(val: Any) -> list[str]:
    """Parse a CSV/JSON field that should be a list of strings.

    Accepts:
    - list[str]
    - "a, b, c" string
    - None → []
    """
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    if isinstance(val, str):
        if not val.strip():
            return []
        # Could be JSON list or comma-separated
        if val.strip().startswith("["):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
        return [p.strip() for p in val.split(",") if p.strip()]
    return []


def _parse_int_cents(val: Any) -> int | None:
    """Parse a baseline price. Accepts dollars (4.99) or cents (499) as int.

    If the value is a float and < 1000, treat as dollars → multiply by 100.
    If the value is an int >= 100, treat as cents.
    """
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val < 1000:
            return int(round(val * 100))
        if isinstance(val, int) and val >= 1000:
            return val
        if isinstance(val, int):
            return val
        return int(round(val * 100))
    s = str(val).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
        if f < 1000:
            return int(round(f * 100))
        return int(round(f))
    except ValueError:
        return None


def import_items_csv(db: Session, csv_content: str, *, dry_run: bool = False) -> ItemImportResult:
    """Import items from CSV text with dedup and per-row errors.

    When dry_run=True: parse all rows, validate, deduplicate, but do NOT
    commit to DB.  Return ItemImportResult with preview populated and
    imported=0.

    When dry_run=False (default): commit each valid row and return result.

    Columns expected: name, category, match_keywords, exclude_keywords,
    preferred_brands, unit_of_measure, typical_quantity, baseline_price_override, active
    """
    errors: list[str] = []
    imported = 0
    skipped = 0
    preview: list[ItemRead] = []

    reader = csv.DictReader(io.StringIO(csv_content))
    if reader.fieldnames is None:
        return ItemImportResult(total_rows=0, imported=0, skipped_duplicates=0, errors=["Empty CSV"])

    for i, row in enumerate(reader, start=2):  # row 2 = first data row (row 1 is header)
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {i}: missing name")
            continue
        # Dedup
        existing = find_item_by_name(db, name)
        if existing:
            skipped += 1
            preview.append(ItemRead.model_validate(existing))
            continue
        try:
            item = Item(
                name=name,
                category=(row.get("category") or "").strip(),
                match_keywords=_parse_list_field(row.get("match_keywords")),
                exclude_keywords=_parse_list_field(row.get("exclude_keywords")),
                preferred_brands=_parse_list_field(row.get("preferred_brands")),
                unit_of_measure=(row.get("unit_of_measure") or "ea").strip(),
                typical_quantity=float(row.get("typical_quantity") or 1.0),
                baseline_price_override=_parse_int_cents(row.get("baseline_price_override") or row.get("baseline_price")),
                active=(row.get("active") or "true").strip().lower() in ("true", "1", "yes"),
            )
            if dry_run:
                # Build a preview ItemRead without committing.
                # The item has no id/created_at yet; synthesize a preview-only copy.
                from datetime import datetime, timezone
                item.id = 0  # placeholder for preview
                item.created_at = datetime.now(timezone.utc)
                preview.append(ItemRead.model_validate(item))
            else:
                db.add(item)
                db.commit()
                db.refresh(item)
                imported += 1
                preview.append(ItemRead.model_validate(item))
        except Exception as exc:
            errors.append(f"Row {i} ({name}): {exc!s}")
            if not dry_run:
                db.rollback()
    return ItemImportResult(
        total_rows=len(preview) + len(errors),
        imported=imported,
        skipped_duplicates=skipped,
        errors=errors,
        preview=preview,
    )


def import_items_json(db: Session, json_content: str, *, dry_run: bool = False) -> ItemImportResult:
    """Import items from a JSON array string.

    When dry_run=True: parse all rows, validate, deduplicate, but do NOT
    commit to DB.  Return ItemImportResult with preview populated and
    imported=0.

    When dry_run=False (default): commit each valid row and return result.
    """
    errors: list[str] = []
    imported = 0
    skipped = 0
    preview: list[ItemRead] = []

    try:
        rows = json.loads(json_content)
    except json.JSONDecodeError as exc:
        return ItemImportResult(total_rows=0, imported=0, skipped_duplicates=0, errors=[f"Invalid JSON: {exc!s}"])

    if not isinstance(rows, list):
        return ItemImportResult(total_rows=0, imported=0, skipped_duplicates=0, errors=["JSON must be an array"])

    for i, row in enumerate(rows, start=1):
        name = (row.get("name") or "").strip() if isinstance(row, dict) else ""
        if not name:
            errors.append(f"Row {i}: missing name")
            continue
        existing = find_item_by_name(db, name)
        if existing:
            skipped += 1
            preview.append(ItemRead.model_validate(existing))
            continue
        try:
            item = Item(
                name=name,
                category=(row.get("category") or "").strip(),
                match_keywords=_parse_list_field(row.get("match_keywords")),
                exclude_keywords=_parse_list_field(row.get("exclude_keywords")),
                preferred_brands=_parse_list_field(row.get("preferred_brands")),
                unit_of_measure=(row.get("unit_of_measure") or "ea").strip(),
                typical_quantity=float(row.get("typical_quantity") or 1.0),
                baseline_price_override=_parse_int_cents(row.get("baseline_price_override") or row.get("baseline_price")),
                active=bool(row.get("active", True)),
            )
            if dry_run:
                from datetime import datetime, timezone
                item.id = 0  # placeholder for preview
                item.created_at = datetime.now(timezone.utc)
                preview.append(ItemRead.model_validate(item))
            else:
                db.add(item)
                db.commit()
                db.refresh(item)
                imported += 1
                preview.append(ItemRead.model_validate(item))
        except Exception as exc:
            errors.append(f"Row {i} ({name}): {exc!s}")
            if not dry_run:
                db.rollback()
    return ItemImportResult(
        total_rows=len(rows),
        imported=imported,
        skipped_duplicates=skipped,
        errors=errors,
        preview=preview,
    )


def export_items_csv(db: Session) -> str:
    """Export all items to CSV text."""
    items = get_items(db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "name",
            "category",
            "match_keywords",
            "exclude_keywords",
            "preferred_brands",
            "unit_of_measure",
            "typical_quantity",
            "baseline_price",
            "active",
        ]
    )
    for item in items:
        writer.writerow(
            [
                item.name,
                item.category,
                ",".join(item.match_keywords or []),
                ",".join(item.exclude_keywords or []),
                ",".join(item.preferred_brands or []),
                item.unit_of_measure,
                item.typical_quantity,
                # Export as dollars if not None
                f"{item.baseline_price_override / 100:.2f}" if item.baseline_price_override else "",
                item.active,
            ]
        )
    return output.getvalue()


def export_items_json(db: Session) -> str:
    """Export all items to a JSON array string."""
    items = get_items(db)
    rows = []
    for item in items:
        rows.append(
            {
                "name": item.name,
                "category": item.category,
                "match_keywords": item.match_keywords or [],
                "exclude_keywords": item.exclude_keywords or [],
                "preferred_brands": item.preferred_brands or [],
                "unit_of_measure": item.unit_of_measure,
                "typical_quantity": item.typical_quantity,
                "baseline_price_override": item.baseline_price_override,
                "active": item.active,
            }
        )
    return json.dumps(rows, indent=2)


def generate_template_csv() -> str:
    """Generate a downloadable CSV template with example rows."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "name",
            "category",
            "match_keywords",
            "exclude_keywords",
            "preferred_brands",
            "unit_of_measure",
            "typical_quantity",
            "baseline_price",
            "active",
        ]
    )
    # Example rows
    writer.writerow(
        [
            "Whole Milk",
            "Dairy",
            "milk, whole",
            "almond, soy, oat",
            "Organic Valley, Horizon",
            "ea",
            "1",
            "4.49",
            "true",
        ]
    )
    writer.writerow(
        [
            "Large Eggs",
            "Dairy",
            "eggs, large",
            "cage free (optional)",
            "",
            "ea",
            "1",
            "3.99",
            "true",
        ]
    )
    writer.writerow(
        [
            "Boneless Chicken Breast",
            "Meat",
            "chicken breast, boneless",
            "thigh, wing, drum",
            "Tyson, Purdue",
            "lb",
            "2",
            "5.99",
            "true",
        ]
    )
    return output.getvalue()


def generate_template_json() -> str:
    """Generate a downloadable JSON template with example rows."""
    template = [
        {
            "name": "Whole Milk",
            "category": "Dairy",
            "match_keywords": ["milk", "whole"],
            "exclude_keywords": ["almond", "soy", "oat"],
            "preferred_brands": ["Organic Valley", "Horizon"],
            "unit_of_measure": "ea",
            "typical_quantity": 1.0,
            "baseline_price_override": 449,
            "active": True,
        },
        {
            "name": "Large Eggs",
            "category": "Dairy",
            "match_keywords": ["eggs", "large"],
            "exclude_keywords": [],
            "preferred_brands": [],
            "unit_of_measure": "ea",
            "typical_quantity": 1.0,
            "baseline_price_override": 399,
            "active": True,
        },
        {
            "name": "Boneless Chicken Breast",
            "category": "Meat",
            "match_keywords": ["chicken breast", "boneless"],
            "exclude_keywords": ["thigh", "wing", "drum"],
            "preferred_brands": ["Tyson", "Purdue"],
            "unit_of_measure": "lb",
            "typical_quantity": 2.0,
            "baseline_price_override": 599,
            "active": True,
        },
    ]
    return json.dumps(template, indent=2)


# ============================================================================
# Offer
# ============================================================================


def get_offers_for_cycle(db: Session, ad_cycle_id: int) -> list[Offer]:
    return list(db.query(Offer).filter(Offer.ad_cycle_id == ad_cycle_id).all())


def get_latest_ad_cycle(db: Session, store_id: int) -> AdCycle | None:
    return (
        db.query(AdCycle)
        .filter(AdCycle.store_id == store_id)
        .order_by(AdCycle.fetched_at.desc())
        .first()
    )


def create_offer(db: Session, offer: Offer) -> Offer:
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


# ============================================================================
# AdCycle
# ============================================================================


def create_ad_cycle(db: Session, cycle: AdCycle) -> AdCycle:
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


# ============================================================================
# Match
# ============================================================================


def get_matches(db: Session, status: MatchStatus | None = None) -> list[Match]:
    q = db.query(Match)
    if status is not None:
        q = q.filter(Match.status == status)
    return list(q.all())


def get_uncertain_matches(db: Session) -> list[Match]:
    return list(
        db.query(Match)
        .filter(Match.status == MatchStatus.uncertain)
        .order_by(Match.confidence.desc())
        .all()
    )


def get_match(db: Session, match_id: int) -> Match | None:
    return db.get(Match, match_id)


def review_match(db: Session, match_id: int, decision: str) -> Match | None:
    """Accept or reject a match.

    decision: "accept" or "reject"

    Also persists a MatchRule (FR-3.2) so the same offer text in future
    cycles auto-applies the decision.
    """
    from app.matching import normalize_offer_text

    match = db.get(Match, match_id)
    if match is None:
        return None
    if decision == "accept":
        match.status = MatchStatus.accepted
        rule_decision = "accepted"
    else:
        match.status = MatchStatus.rejected
        rule_decision = "rejected"
    match.decided_by = MatchDecidedBy.user
    db.commit()
    db.refresh(match)

    # Persist a MatchRule keyed on (item_id, normalized offer text) so future
    # cycles auto-apply this decision (FR-3.2).
    # NOTE: must use the SAME source of truth as process_matches
    # (product_name first, then raw_text) or the rule key won't match
    # on the next refresh and the same match reappears.
    offer = db.get(Offer, match.offer_id) if match.offer_id else None
    if offer is not None:
        normalized = normalize_offer_text(offer.product_name or offer.raw_text or "")
        if normalized:
            create_match_rule(db, item_id=match.item_id, normalized_offer_text=normalized, decision=rule_decision)

    db.refresh(match)
    return match


# ============================================================================
# MatchRule (FR-3.2)
# ============================================================================


def create_match_rule(
    db: Session,
    item_id: int,
    normalized_offer_text: str,
    decision: str,
) -> MatchRule:
    """Upsert a MatchRule for (item_id, normalized_offer_text).

    If a rule already exists for this key, update its decision; otherwise create
    a new one.
    """
    existing = (
        db.query(MatchRule)
        .filter(
            MatchRule.item_id == item_id,
            MatchRule.normalized_offer_text == normalized_offer_text,
        )
        .first()
    )
    if existing is not None:
        existing.decision = decision
        db.commit()
        db.refresh(existing)
        return existing
    rule = MatchRule(
        item_id=item_id,
        normalized_offer_text=normalized_offer_text,
        decision=decision,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def get_match_rule(
    db: Session,
    item_id: int,
    normalized_offer_text: str,
) -> MatchRule | None:
    """Check if a MatchRule exists for (item_id, normalized_offer_text)."""
    return (
        db.query(MatchRule)
        .filter(
            MatchRule.item_id == item_id,
            MatchRule.normalized_offer_text == normalized_offer_text,
        )
        .first()
    )


def get_match_rules_for_item(db: Session, item_id: int) -> list[MatchRule]:
    """Get all MatchRules for an item."""
    return list(db.query(MatchRule).filter(MatchRule.item_id == item_id).all())


def count_uncertain(db: Session) -> int:
    return (
        db.query(Match)
        .filter(Match.status == MatchStatus.uncertain)
        .count()
    )


# ============================================================================
# PriceHistory
# ============================================================================


def get_price_history(db: Session, item_id: int) -> list[PriceHistory]:
    return list(
        db.query(PriceHistory)
        .filter(PriceHistory.item_id == item_id)
        .order_by(PriceHistory.week.desc())
        .all()
    )


def upsert_price_history(
    db: Session,
    item_id: int,
    store_id: int,
    week: date,
    best_unit_price: int,
    deal_type: str,
) -> PriceHistory:
    """Insert or update a price history row."""
    existing = db.get(PriceHistory, {"item_id": item_id, "store_id": store_id, "week": week})
    if existing:
        existing.best_unit_price = best_unit_price
        existing.deal_type = deal_type
    else:
        existing = PriceHistory(
            item_id=item_id,
            store_id=store_id,
            week=week,
            best_unit_price=best_unit_price,
            deal_type=deal_type,
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


# ============================================================================
# WeeklyReport
# ============================================================================


def get_weekly_report(db: Session, week: date) -> WeeklyReport | None:
    return db.get(WeeklyReport, week)


def get_latest_weekly_report(db: Session) -> WeeklyReport | None:
    return db.query(WeeklyReport).order_by(WeeklyReport.week.desc()).first()


def get_all_weekly_reports(db: Session, limit: int = 52) -> list[WeeklyReport]:
    return list(
        db.query(WeeklyReport).order_by(WeeklyReport.week.desc()).limit(limit).all()
    )


def upsert_weekly_report(
    db: Session,
    week: date,
    best_single_store_id: int | None,
    best_pair_store_ids: list[int],
    projected_savings_single: int,
    projected_savings_pair: int,
    per_item_results_json: str,
) -> WeeklyReport:
    existing = db.get(WeeklyReport, week)
    if existing:
        existing.best_single_store_id = best_single_store_id
        existing.best_pair_store_ids = best_pair_store_ids
        existing.projected_savings_single = projected_savings_single
        existing.projected_savings_pair = projected_savings_pair
        existing.per_item_results_json = per_item_results_json
    else:
        existing = WeeklyReport(
            week=week,
            best_single_store_id=best_single_store_id,
            best_pair_store_ids=best_pair_store_ids,
            projected_savings_single=projected_savings_single,
            projected_savings_pair=projected_savings_pair,
            per_item_results_json=per_item_results_json,
        )
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


# ============================================================================
# Settings
# ============================================================================


DEFAULT_SETTINGS: dict[str, str] = {
    "two_store_threshold": "500",  # $5.00 in cents
    "baseline_strategy": "auto",   # auto | manual | mixed
    "refresh_schedule": "07:00",   # HH:MM
}


def get_setting(db: Session, key: str) -> str:
    setting = db.get(Setting, key)
    if setting is None:
        return DEFAULT_SETTINGS.get(key, "")
    return setting.value


def set_setting(db: Session, key: str, value: str) -> Setting:
    setting = db.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    db.commit()
    db.refresh(setting)
    return setting


def get_all_settings(db: Session) -> dict[str, str]:
    """Return all settings merged with defaults."""
    settings = {}
    settings.update(DEFAULT_SETTINGS)
    for s in db.query(Setting).all():
        settings[s.key] = s.value
    return settings


def set_settings(db: Session, settings: dict[str, str]) -> dict[str, str]:
    for key, value in settings.items():
        set_setting(db, key, value)
    return get_all_settings(db)


# ============================================================================
# Cumulative savings
# ============================================================================


def get_cumulative_savings(db: Session) -> int:
    """Sum of all projected_savings_single from weekly reports."""
    reports = db.query(WeeklyReport).all()
    return sum(r.projected_savings_single for r in reports)
