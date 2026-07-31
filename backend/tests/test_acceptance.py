"""Acceptance-criteria tests covering spec §6 (criteria 1–10).

These tests exercise the full stack through the FastAPI TestClient and
also drop into the DB layer directly to set up offers/matches/price
history without calling real store adapters (which need external APIs).
"""

from __future__ import annotations

import io
from datetime import date, timedelta

from app.models import (
    AdCycle,
    Item,
    Match,
    MatchDecidedBy,
    MatchStatus,
    Offer,
    PriceHistory,
    Store,
)


# =========================================================================== #
# Test-data helpers
# =========================================================================== #

def _enable_store(db, adapter_key: str) -> Store:
    """Enable a seeded store and return it."""
    from app.crud import get_store_by_adapter_key
    store = get_store_by_adapter_key(db, adapter_key)
    assert store is not None, f"seeded store {adapter_key!r} missing"
    store.enabled = True
    db.commit()
    db.refresh(store)
    return store


def _create_item(db, name="Milk", category="Dairy", uom="ea", qty=1.0,
                 keywords=None, exclude=None, brands=None, baseline=None):
    item = Item(
        name=name,
        category=category,
        match_keywords=keywords or [],
        exclude_keywords=exclude or [],
        preferred_brands=brands or [],
        unit_of_measure=uom,
        typical_quantity=qty,
        baseline_price_override=baseline,
        active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _create_cycle(db, store: Store, days_ago=0) -> AdCycle:
    today = date.today() - timedelta(days=days_ago)
    cycle = AdCycle(
        store_id=store.id,
        period_start=today,
        period_end=today + timedelta(days=6),
        fetched_at=__import__("datetime").datetime.utcnow(),
        raw_payload_ref="",
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


def _create_offer(db, cycle: AdCycle, raw_text="$2.99", product_name="",
                 brand="", size_text="", price=None, deal_type="sale",
                 price_per_item=None, price_per_oz=None) -> Offer:
    from app.matching import build_offer
    offer = build_offer(
        ad_cycle_id=cycle.id,
        raw_text=raw_text,
        product_name=product_name,
        brand=brand,
        size_text=size_text,
    )
    # Override explicit values if provided (build_offer parses from text).
    if price is not None:
        offer.price = price
    if deal_type:
        offer.deal_type = deal_type
    if price_per_item is not None:
        offer.price_per_item_cents = price_per_item
    if price_per_oz is not None:
        offer.price_per_oz_cents = price_per_oz
    db.add(offer)
    db.commit()
    db.refresh(offer)
    return offer


def _create_match(db, offer: Offer, item: Item, status=MatchStatus.confident,
                  confidence=90.0) -> Match:
    m = Match(
        offer_id=offer.id,
        item_id=item.id,
        confidence=confidence,
        status=status,
        decided_by=MatchDecidedBy.auto,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def _add_history(db, item_id, store_id, week, price, deal_type="sale"):
    h = PriceHistory(
        item_id=item_id, store_id=store_id, week=week,
        best_unit_price=price, deal_type=deal_type,
    )
    db.add(h)
    db.commit()
    return h


# =========================================================================== #
# Criterion 1: 20-item imported list, stores enabled, dashboard shows
# per-item best prices with store attribution.
# =========================================================================== #

def test_c1_dashboard_shows_best_prices_with_stores(client, db):
    """All 7 stores enabled + 20 items + offers → dashboard has
    store_statuses and best_prices with per-item rows."""
    # Enable all seeded stores
    from app.crud import get_stores
    for s in get_stores(db):
        s.enabled = True
    db.commit()

    # Import 20 items via CSV (spec §7 template + extras to reach 20)
    csv_lines = [
        "name,category,match_keywords,exclude_keywords,preferred_brands,unit_of_measure,typical_quantity,baseline_price,active",
    ]
    base_items = [
        ("Boneless skinless chicken breast", "Meat", "chicken breast", "thighs", "", "lb", "3", "3.99"),
        ("Greek yogurt 32oz", "Dairy", "greek yogurt", "", "Chobani", "oz", "32", "5.99"),
        ("Large eggs", "Dairy", "eggs", "", "", "dozen", "2", "3.49"),
        ("Avocados", "Produce", "avocado", "", "", "each", "4", "1.25"),
        ("Ground coffee 12oz", "Pantry", "ground coffee", "k-cup", "", "oz", "12", "9.99"),
        ("Whole milk", "Dairy", "milk whole", "almond", "", "ea", "1", "4.49"),
        ("Bananas", "Produce", "banana", "", "", "lb", "2", "0.59"),
        ("Sliced bread", "Pantry", "bread", "", "", "ea", "1", "2.99"),
        ("Cheddar cheese 8oz", "Dairy", "cheddar cheese", "", "", "oz", "8", "4.49"),
        ("Pasta sauce", "Pantry", "pasta sauce", "", "", "ea", "1", "2.79"),
        ("Ground beef 1lb", "Meat", "ground beef", "", "", "lb", "2", "5.99"),
        ("Orange juice", "Pantry", "orange juice", "", "", "ea", "1", "3.99"),
        ("Butter", "Dairy", "butter", "", "", "ea", "1", "4.99"),
        ("Cereal", "Pantry", "cereal", "", "", "ea", "1", "3.49"),
        ("Salmon fillet", "Meat", "salmon", "", "", "lb", "1", "12.99"),
        ("Tortilla chips", "Pantry", "tortilla chips", "", "", "ea", "1", "2.99"),
        ("Salsa", "Pantry", "salsa", "", "", "ea", "1", "3.29"),
        ("Paper towels", "Household", "paper towels", "", "", "ea", "1", "6.99"),
        ("Dish soap", "Household", "dish soap", "", "", "ea", "1", "3.49"),
        ("Frozen pizza", "Frozen", "frozen pizza", "", "", "ea", "1", "5.99"),
    ]
    assert len(base_items) == 20
    for name, cat, kw, ex, br, uom, qty, base in base_items:
        csv_lines.append(f'{name},{cat},"{kw}","{ex}","{br}",{uom},{qty},{base},true')
    csv_content = "\n".join(csv_lines)

    # Import via API
    r = client.post(
        "/api/items/import/csv",
        files={"file": ("items.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["imported"] == 20, f"expected 20 imported, got {result}"

    # Verify 20 items exist
    r = client.get("/api/items?active=true")
    assert len(r.json()) == 20

    # Add a few offers + confident matches for a subset so the dashboard
    # has best_prices data.  We simulate the refresh by inserting offers
    # and matches directly (no external adapter calls).
    from app.crud import get_items, get_store_by_adapter_key
    items = get_items(db, active_only=True)
    stores = [get_store_by_adapter_key(db, k) for k in
              ("aldi", "walmart", "jewel_osco")]
    for i, item in enumerate(items[:5]):
        store = stores[i % len(stores)]
        cycle = _create_cycle(db, store)
        offer = _create_offer(db, cycle, raw_text=f"${3.99 - i * 0.20:.2f} {item.name}")
        _create_match(db, offer, item, status=MatchStatus.confident, confidence=90.0)

    # GET dashboard
    r = client.get("/api/dashboard")
    assert r.status_code == 200, r.text
    dash = r.json()
    assert "store_statuses" in dash
    assert len(dash["store_statuses"]) == 7
    assert "best_prices" in dash
    assert dash["best_prices"] is not None
    # We matched 5 items → 5 with_deals
    assert len(dash["best_prices"]["items_with_deals"]) == 5
    # Each deal row has store attribution
    for entry in dash["best_prices"]["items_with_deals"]:
        assert entry["current_best_store_name"]
        assert entry["current_best_price"] is not None


# =========================================================================== #
# Criterion 2: Item on sale at 3 stores → lowest unit price is best,
# other 2 visible in the expanded row.
# =========================================================================== #

def test_c2_lowest_unit_price_best_others_in_expanded_row(client, db):
    from app.crud import get_store_by_adapter_key
    item = _create_item(db, name="Whole Milk", uom="ea",
                       keywords=["milk", "whole"], baseline=500)
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")
    s3 = _enable_store(db, "jewel_osco")

    # Three offers at different per-item prices
    offers_prices = [(s1, 299), (s2, 349), (s3, 319)]
    for store, unit_price in offers_prices:
        cycle = _create_cycle(db, store)
        offer = _create_offer(db, cycle, raw_text=f"${unit_price / 100:.2f} Whole Milk",
                              price_per_item=unit_price, price=unit_price)
        _create_match(db, offer, item, confidence=95.0)

    r = client.get("/api/best-prices")
    assert r.status_code == 200, r.text
    bp = r.json()
    assert len(bp["items_with_deals"]) == 1
    entry = bp["items_with_deals"][0]
    # Lowest (299) is current best
    assert entry["current_best_price"] == 299
    assert entry["current_best_store_name"] == "Aldi"
    # Other 2 stores visible in other_store_prices
    assert len(entry["other_store_prices"]) == 2
    other_prices = sorted(o["price"] for o in entry["other_store_prices"])
    assert other_prices == [319, 349]


# =========================================================================== #
# Criterion 3: "2 for $6" on 32oz → $3.00 ea, $0.09/oz and can win/lose.
# =========================================================================== #

def test_c3_two_for_six_unit_price_math(client, db):
    from app.matching import normalize_price
    np = normalize_price("2 for $6", size_text="32 oz")
    assert np.price_per_item_cents == 300  # $3.00 ea
    assert np.price_per_oz_cents == 9      # 600/64 = 9.375 → 9


def test_c3_two_for_six_wins_on_unit_price(client, db):
    """A count item: 2-for-$6 offer ($3/ea) beats a $3.49 offer."""
    from app.crud import get_store_by_adapter_key
    item = _create_item(db, name="Cereal 18oz", uom="ea",
                       keywords=["cereal"], baseline=400)
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    c1 = _create_cycle(db, s1)
    offer1 = _create_offer(db, c1, raw_text="2 for $6 Cereal", size_text="32 oz")
    _create_match(db, offer1, item, confidence=95.0)

    c2 = _create_cycle(db, s2)
    offer2 = _create_offer(db, c2, raw_text="$3.49 Cereal", price=349,
                          price_per_item=349)
    _create_match(db, offer2, item, confidence=95.0)

    r = client.get("/api/best-prices")
    entry = r.json()["items_with_deals"][0]
    # per-item 300 < 349 → aldi wins
    assert entry["current_best_price"] == 300
    assert entry["current_best_store_name"] == "Aldi"


def test_c3_two_for_six_loses_on_unit_price(client, db):
    """A count item: 2-for-$6 ($3/ea) loses to a $2.79 offer."""
    from app.crud import get_store_by_adapter_key
    item = _create_item(db, name="Cereal 18oz", uom="ea",
                       keywords=["cereal"], baseline=400)
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    c1 = _create_cycle(db, s1)
    offer1 = _create_offer(db, c1, raw_text="2 for $6 Cereal", size_text="32 oz")
    _create_match(db, offer1, item, confidence=95.0)

    c2 = _create_cycle(db, s2)
    offer2 = _create_offer(db, c2, raw_text="$2.79 Cereal", price=279,
                          price_per_item=279)
    _create_match(db, offer2, item, confidence=95.0)

    r = client.get("/api/best-prices")
    entry = r.json()["items_with_deals"][0]
    assert entry["current_best_price"] == 279
    assert entry["current_best_store_name"] == "Walmart"


# =========================================================================== #
# Criterion 4: Last-best-price delta → better, worse, unchanged, new.
# =========================================================================== #

def test_c4_delta_directions(client, db):
    from app.crud import get_store_by_adapter_key
    from app.recommendations import compute_best_prices

    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")
    today = date.today()
    last_week = today - timedelta(days=7)

    # Item A: better (was 399 last week, now 299)
    a = _create_item(db, name="Item A Better", keywords=["itema"], baseline=500)
    _add_history(db, a.id, s1.id, last_week, 399, "sale")
    c = _create_cycle(db, s1)
    offer = _create_offer(db, c, raw_text="$2.99 Item A", price=299, price_per_item=299)
    _create_match(db, offer, a, confidence=95.0)

    # Item B: worse (was 199 last week, now 299)
    b = _create_item(db, name="Item B Worse", keywords=["itemb"], baseline=500)
    _add_history(db, b.id, s2.id, last_week, 199, "sale")
    c2 = _create_cycle(db, s2)
    offer2 = _create_offer(db, c2, raw_text="$2.99 Item B", price=299, price_per_item=299)
    _create_match(db, offer2, b, confidence=95.0)

    # Item C: unchanged (was 299 last week, now 299)
    cc = _create_item(db, name="Item C Same", keywords=["itemc"], baseline=500)
    _add_history(db, cc.id, s1.id, last_week, 299, "sale")
    c3 = _create_cycle(db, s1)
    offer3 = _create_offer(db, c3, raw_text="$2.99 Item C", price=299, price_per_item=299)
    _create_match(db, offer3, cc, confidence=95.0)

    # Item D: first-ever (no history) → "new"
    dd = _create_item(db, name="Item D New", keywords=["itemd"], baseline=500)
    c4 = _create_cycle(db, s2)
    offer4 = _create_offer(db, c4, raw_text="$3.49 Item D", price=349, price_per_item=349)
    _create_match(db, offer4, dd, confidence=95.0)

    bp = compute_best_prices(db)
    by_name = {e.item_name: e for e in bp.items_with_deals}

    assert by_name["Item A Better"].delta_direction == "better"
    assert by_name["Item B Worse"].delta_direction == "worse"
    assert by_name["Item C Same"].delta_direction == "unchanged"
    assert by_name["Item D New"].delta_direction == "new"


# =========================================================================== #
# Criterion 5: Single & two-store recommendations mathematically verifiable;
# two-store split-list totals match reported savings.
# =========================================================================== #

def test_c5_single_store_recommendation_math(client, db):
    from app.crud import get_store_by_adapter_key
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    # Two items with baselines; aldi has a sale on item1
    i1 = _create_item(db, name="Milk", baseline=500, qty=2)
    i2 = _create_item(db, name="Bread", baseline=300, qty=1)

    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Milk", price=299, price_per_item=299)
    _create_match(db, o1, i1, confidence=95.0)

    c2 = _create_cycle(db, s2)
    # No offers for aldi on bread; no offers for walmart at all → baselines

    r = client.get("/api/recommendations")
    assert r.status_code == 200, r.text
    recs = r.json()
    assert recs["best_single"] is not None

    # Find the aldi single-store result
    aldi = next(s for s in recs["single"] if s["store_name"] == "Aldi")
    # Milk: 299 * 2 = 598; Bread: no sale at aldi → baseline 300 * 1 = 300
    # total = 598 + 300 = 898
    assert aldi["total_cost"] == 898
    # baseline = (500*2) + (300*1) = 1300
    assert aldi["baseline_cost"] == 1300
    # savings = 1300 - 898 = 402
    assert aldi["savings"] == 402


def test_c5_two_store_split_totals_match_savings(client, db):
    from app.crud import get_store_by_adapter_key
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    i1 = _create_item(db, name="Milk", baseline=500, qty=1)
    i2 = _create_item(db, name="Bread", baseline=300, qty=1)

    # aldi: milk $2.99 (sale), bread baseline
    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Milk", price=299, price_per_item=299)
    _create_match(db, o1, i1, confidence=95.0)

    # walmart: bread $1.99 (sale), milk baseline
    c2 = _create_cycle(db, s2)
    o2 = _create_offer(db, c2, raw_text="$1.99 Bread", price=199, price_per_item=199)
    _create_match(db, o2, i2, confidence=95.0)

    r = client.get("/api/recommendations")
    recs = r.json()
    best_pair = recs["best_pair"]
    assert best_pair is not None

    # Pair picks cheapest per item: milk→aldi 299, bread→walmart 199
    # total = 299 + 199 = 498; baseline = 500 + 300 = 800; savings = 302
    assert best_pair["total_cost"] == 498
    assert best_pair["baseline_cost"] == 800
    assert best_pair["savings"] == 302

    # Verify the split list (item_store_map) matches the line totals
    detail_by_item = {d["item_id"]: d for d in best_pair["details"]}
    computed_total = sum(d["line_total"] for d in best_pair["details"])
    assert computed_total == best_pair["total_cost"]
    assert detail_by_item[i1.id]["line_total"] == 299
    assert detail_by_item[i2.id]["line_total"] == 199


# =========================================================================== #
# Criterion 6: CSV import with 1 bad row imports good rows, reports the bad.
# =========================================================================== #

def test_c6_csv_import_one_bad_row(client):
    csv_content = (
        "name,category,match_keywords,exclude_keywords,preferred_brands,unit_of_measure,typical_quantity,baseline_price,active\n"
        "Whole Milk,Dairy,\"milk, whole\",\"almond, soy\",\"Organic Valley\",ea,1,4.49,true\n"
        "Large Eggs,Dairy,\"eggs, large\",\"\",\"\",ea,1,3.99,true\n"
        ",,,,\n"  # BAD ROW: empty name
        "Avocados,Produce,avocado,,ea,4,1.25,true\n"
    )
    r = client.post(
        "/api/items/import/csv",
        files={"file": ("items.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["imported"] == 3  # 3 good rows
    assert len(result["errors"]) >= 1
    assert any("missing name" in e for e in result["errors"])


# =========================================================================== #
# Criterion 7: A store fetch failure shows "failed" without affecting
# other stores.
# =========================================================================== #

def test_c7_store_fetch_failure_isolated(client, db):
    from app.crud import get_store_by_adapter_key, update_store_status
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    # Simulate: aldi fails, walmart ok
    update_store_status(db, s1.id, "failed")
    update_store_status(db, s2.id, "ok",
                        fetched_at=__import__("datetime").datetime.utcnow())

    r = client.get("/api/dashboard")
    assert r.status_code == 200
    statuses = {s["name"]: s["last_fetch_status"] for s in r.json()["store_statuses"]}
    assert statuses["Aldi"] == "failed"
    # Walmart "ok" within 24h stays ok (compute_stale_status)
    assert statuses["Walmart"] in ("ok", "stale")


# =========================================================================== #
# Criterion 8: Review-queue accept/reject immediately updates best prices.
# =========================================================================== #

def test_c8_accept_uncertain_updates_best_prices(client, db):
    from app.crud import get_store_by_adapter_key
    item = _create_item(db, name="Greek Yogurt", uom="ea",
                       keywords=["greek", "yogurt"], baseline=500)
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    # aldi: uncertain match at $2.99
    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Greek Yogurt", price=299,
                       price_per_item=299)
    m_uncertain = _create_match(db, o1, item, status=MatchStatus.uncertain,
                                confidence=60.0)

    # walmart: confident match at $3.49
    c2 = _create_cycle(db, s2)
    o2 = _create_offer(db, c2, raw_text="$3.49 Greek Yogurt", price=349,
                       price_per_item=349)
    _create_match(db, o2, item, confidence=95.0)

    # Before accept: uncertain doesn't count → walmart $3.49 is best
    r = client.get("/api/best-prices")
    entry = r.json()["items_with_deals"][0]
    assert entry["current_best_price"] == 349
    assert entry["current_best_store_name"] == "Walmart"

    # Accept the uncertain match
    r = client.post(f"/api/matches/{m_uncertain.id}/decide",
                   json={"decision": "accept"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    # After accept: aldi $2.99 should now be best
    r = client.get("/api/best-prices")
    entry = r.json()["items_with_deals"][0]
    assert entry["current_best_price"] == 299
    assert entry["current_best_store_name"] == "Aldi"


def test_c8_reject_uncertain_keeps_best_unchanged(client, db):
    from app.crud import get_store_by_adapter_key
    item = _create_item(db, name="Greek Yogurt", uom="ea",
                       keywords=["greek", "yogurt"], baseline=500)
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")

    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Greek Yogurt", price=299,
                       price_per_item=299)
    m_uncertain = _create_match(db, o1, item, status=MatchStatus.uncertain,
                                confidence=60.0)

    c2 = _create_cycle(db, s2)
    o2 = _create_offer(db, c2, raw_text="$3.49 Greek Yogurt", price=349,
                       price_per_item=349)
    _create_match(db, o2, item, confidence=95.0)

    # Reject the uncertain match
    r = client.post(f"/api/matches/{m_uncertain.id}/decide",
                   json={"decision": "reject"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # Best price still walmart
    r = client.get("/api/best-prices")
    entry = r.json()["items_with_deals"][0]
    assert entry["current_best_price"] == 349


# =========================================================================== #
# Criterion 9: Cumulative savings persists across app restarts.
# =========================================================================== #

def test_c9_cumulative_savings_persists(client, db, tmp_path):
    from app.recommendations import generate_weekly_report
    from app.crud import get_store_by_adapter_key

    s1 = _enable_store(db, "aldi")
    item = _create_item(db, name="Milk", baseline=500, qty=2)
    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Milk", price=299, price_per_item=299)
    _create_match(db, o1, item, confidence=95.0)

    # Generate a weekly report → persists projected_savings_single
    generate_weekly_report(db, week=date.today())

    r = client.get("/api/savings")
    assert r.status_code == 200, r.text
    savings = r.json()
    assert savings["cumulative_savings"] > 0
    assert savings["weekly_report"] is not None

    # "Restart" = re-read savings; since it's persisted in the DB table,
    # a fresh session still reports the same cumulative total.
    from app.database import SessionLocal
    from app.crud import get_cumulative_savings
    fresh_session = SessionLocal()
    try:
        again = get_cumulative_savings(fresh_session)
    finally:
        fresh_session.close()
    assert again == savings["cumulative_savings"]


# =========================================================================== #
# Criterion 10: Shopping list returns correct grouped entries.
# =========================================================================== #

def test_c10_shopping_list_single_mode(client, db):
    from app.crud import get_store_by_adapter_key
    s1 = _enable_store(db, "aldi")
    i1 = _create_item(db, name="Milk", baseline=500, qty=2)
    i2 = _create_item(db, name="Bread", baseline=300, qty=1)

    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Milk", price=299, price_per_item=299)
    _create_match(db, o1, i1, confidence=95.0)

    r = client.get("/api/shopping-list?mode=single")
    assert r.status_code == 200, r.text
    sl = r.json()
    assert sl["mode"] == "single"
    assert sl["total_cost"] > 0
    # Two entries (milk on sale, bread baseline)
    assert len(sl["entries"]) == 2
    names = {e["item_name"] for e in sl["entries"]}
    assert names == {"Milk", "Bread"}
    # Milk entry is a sale
    milk_entry = next(e for e in sl["entries"] if e["item_name"] == "Milk")
    assert milk_entry["is_sale"] is True
    assert milk_entry["line_total"] == 598  # 299 * 2


def test_c10_shopping_list_pair_mode(client, db):
    from app.crud import get_store_by_adapter_key
    s1 = _enable_store(db, "aldi")
    s2 = _enable_store(db, "walmart")
    i1 = _create_item(db, name="Milk", baseline=500, qty=1)
    i2 = _create_item(db, name="Bread", baseline=300, qty=1)

    c1 = _create_cycle(db, s1)
    o1 = _create_offer(db, c1, raw_text="$2.99 Milk", price=299, price_per_item=299)
    _create_match(db, o1, i1, confidence=95.0)

    c2 = _create_cycle(db, s2)
    o2 = _create_offer(db, c2, raw_text="$1.99 Bread", price=199, price_per_item=199)
    _create_match(db, o2, i2, confidence=95.0)

    r = client.get("/api/shopping-list?mode=pair")
    assert r.status_code == 200, r.text
    sl = r.json()
    assert sl["mode"] == "pair"
    assert len(sl["store_ids"]) == 2
    # Entries grouped by store
    by_store = {}
    for e in sl["entries"]:
        by_store.setdefault(e["store_name"], []).append(e)
    assert "Aldi" in by_store and "Walmart" in by_store
    assert any(e["item_name"] == "Milk" for e in by_store["Aldi"])
    assert any(e["item_name"] == "Bread" for e in by_store["Walmart"])
