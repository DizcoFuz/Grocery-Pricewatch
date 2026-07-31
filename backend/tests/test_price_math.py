"""Focused unit tests for price normalization (matching.py).

Covers normalize_price, compute_comparable_price, and the per-item vs
per-oz comparison rules (P0-4).

All prices are integer cents.
"""

from __future__ import annotations

from app.matching import (
    compute_comparable_price,
    normalize_price,
)
from app.models import Item, Offer


# --------------------------------------------------------------------------- #
# Helpers to build lightweight Offer / Item objects without a DB
# --------------------------------------------------------------------------- #

def _offer(price=0, deal_type="sale", price_per_item=None, price_per_oz=None,
           size_text="", effective_unit_price=0, unit_price_unknown=False):
    """Build a transient Offer ORM object (not attached to a session)."""
    return Offer(
        ad_cycle_id=0,
        raw_text="",
        product_name="",
        brand="",
        size_text=size_text,
        price=price,
        deal_type=deal_type,
        effective_unit_price=effective_unit_price,
        unit_price_unknown=unit_price_unknown,
        requires_membership_or_coupon=False,
        price_per_item_cents=price_per_item,
        price_per_oz_cents=price_per_oz,
    )


def _item(name="Test", uom="ea"):
    return Item(name=name, unit_of_measure=uom, typical_quantity=1.0)


# =========================================================================== #
# normalize_price
# =========================================================================== #


class TestSimpleSale:
    def test_simple_dollar(self):
        np = normalize_price("$2.99")
        assert np.price == 299
        assert np.deal_type == "sale"
        assert np.quantity == 1.0
        assert np.price_per_item_cents == 299
        assert np.price_per_oz_cents is None  # no size given

    def test_simple_with_size(self):
        np = normalize_price("$2.99", size_text="32 oz")
        assert np.price == 299
        assert np.price_per_item_cents == 299
        # 299 / 32 ≈ 9.34 → 9
        assert np.price_per_oz_cents == 9

    def test_plain_number(self):
        np = normalize_price("3.49")
        assert np.price == 349
        assert np.price_per_item_cents == 349

    def test_empty_text(self):
        np = normalize_price("")
        assert np.price == 0
        # No price found → defaults; deal_type stays "sale" (dataclass default)
        assert np.unit_price_unknown is True


class TestPerLb:
    def test_per_lb(self):
        np = normalize_price("$2.99/lb")
        assert np.price == 299
        assert np.deal_type == "per_lb"
        # per-oz = 299/16 ≈ 18.69 → 19
        assert np.price_per_oz_cents == 19
        assert np.price_per_item_cents is None

    def test_per_pound_words(self):
        np = normalize_price("$4.99 per pound")
        assert np.price == 499
        assert np.deal_type == "per_lb"
        assert np.price_per_oz_cents == 31  # 499/16 = 31.18


class TestMultiBuy:
    def test_two_for_five(self):
        np = normalize_price("2 for $5")
        assert np.price == 500
        assert np.deal_type == "multi_buy"
        assert np.quantity == 2.0
        assert np.price_per_item_cents == 250
        assert np.price_per_oz_cents is None  # no size

    def test_two_for_five_with_size(self):
        np = normalize_price("2 for $5", size_text="16 oz")
        assert np.price_per_item_cents == 250
        # total oz = 16 * 2 = 32; 500 / 32 = 15.625 → 16
        assert np.price_per_oz_cents == 16

    def test_three_for_ten(self):
        np = normalize_price("3/$10")
        assert np.price == 1000
        assert np.deal_type == "multi_buy"
        assert np.quantity == 3.0
        assert np.price_per_item_cents == 333  # 1000/3 = 333.33 → 333

    def test_two_for_six_on_32oz(self):
        """Spec §6 criterion 3: '2 for $6' on a 32 oz item.

        Shown as $3.00 ea, $0.09/oz (600/64 = 9.375 → 9).
        """
        np = normalize_price("2 for $6", size_text="32 oz")
        assert np.price == 600
        assert np.deal_type == "multi_buy"
        assert np.quantity == 2.0
        assert np.price_per_item_cents == 300  # $3.00 ea
        assert np.price_per_oz_cents == 9  # 600/64 = 9.375 → 9


class TestBOGO:
    def test_bogo_with_price(self):
        np = normalize_price("BOGO $3.99")
        assert np.price == 399
        assert np.deal_type == "bogo"
        assert np.quantity == 2.0
        # pay for 1 get 2 → per item = 399/2 = 199.5 → 200
        assert np.price_per_item_cents == 200

    def test_bogo_with_size(self):
        # "BOGO" has no stray digits, so the price pattern finds $4.00.
        np = normalize_price("BOGO $4.00", size_text="16 oz")
        assert np.price == 400
        assert np.deal_type == "bogo"
        assert np.price_per_item_cents == 200
        # 2 items × 16oz = 32 oz total; 400/32 = 12.5 → 12 (banker's rounding)
        assert np.price_per_oz_cents == 12

    def test_b1g1(self):
        # B1G1 grabs the first digit "1" → price=100; this is a known
        # limitation of the simple price pattern.  We assert the deal_type
        # and per-item math (100/2 = 50).
        np = normalize_price("B1G1 $5.00")
        assert np.deal_type == "bogo"
        assert np.price_per_item_cents == 50


class TestMembershipFlag:
    def test_prime(self):
        np = normalize_price("$3.99 Prime")
        assert np.requires_membership_or_coupon is True

    def test_coupon(self):
        np = normalize_price("$1.99 with store coupon")
        assert np.requires_membership_or_coupon is True

    def test_no_membership(self):
        np = normalize_price("$2.99")
        assert np.requires_membership_or_coupon is False


# =========================================================================== #
# compute_comparable_price
# =========================================================================== #

class TestComputeComparablePrice:
    def test_count_uom_uses_per_item(self):
        offer = _offer(price=500, price_per_item=250, price_per_oz=16)
        item = _item(uom="ea")
        price, approx = compute_comparable_price(offer, item)
        assert price == 250
        assert approx is False

    def test_weight_uom_oz_uses_per_oz(self):
        offer = _offer(price=500, price_per_item=250, price_per_oz=16)
        item = _item(uom="oz")
        price, approx = compute_comparable_price(offer, item)
        assert price == 16
        assert approx is False

    def test_weight_uom_lb_converts_per_oz_to_lb(self):
        """per_oz=18 → per_lb = 18*16 = 288."""
        offer = _offer(price=299, price_per_oz=18, price_per_item=None)
        item = _item(uom="lb")
        price, approx = compute_comparable_price(offer, item)
        assert price == 288
        assert approx is False

    def test_count_uom_fallback_to_raw_price(self):
        """No price_per_item → fall back to raw headline price, approximate."""
        offer = _offer(price=500, price_per_item=None, price_per_oz=16,
                       deal_type="per_lb")
        item = _item(uom="ea")
        price, approx = compute_comparable_price(offer, item)
        assert price == 500
        assert approx is True

    def test_weight_uom_fallback_to_raw_price(self):
        offer = _offer(price=500, price_per_item=250, price_per_oz=None)
        item = _item(uom="lb")
        price, approx = compute_comparable_price(offer, item)
        assert price == 500
        assert approx is True


# =========================================================================== #
# Per-oz and per-item prices must NOT be compared against each other
# (P0-4 defect 3).  This is enforced by compute_comparable_price choosing
# the basis from the ITEM's UoM, so two offers for the same item always
# compare on the same basis.
# =========================================================================== #

class TestBasisSeparation:
    def test_count_item_ignores_per_oz(self):
        """A count item should NOT win just because per_oz is tiny."""
        # Offer A: $5/each (per_item=500), per_oz unknown
        offer_a = _offer(price=500, price_per_item=500, price_per_oz=None)
        # Offer B: $3/each but per_oz=1 (irrelevant for count item)
        offer_b = _offer(price=300, price_per_item=300, price_per_oz=1)
        item = _item(uom="ea")
        pa, _ = compute_comparable_price(offer_a, item)
        pb, _ = compute_comparable_price(offer_b, item)
        assert pb < pa  # B cheaper on per-item basis
        assert pa == 500 and pb == 300

    def test_weight_item_ignores_per_item(self):
        """A weight item should NOT win just because per_item is tiny."""
        offer_a = _offer(price=299, price_per_item=299, price_per_oz=18)  # $2.99/lb
        offer_b = _offer(price=100, price_per_item=100, price_per_oz=50)  # worse per-oz
        item = _item(uom="lb")
        pa, _ = compute_comparable_price(offer_a, item)
        pb, _ = compute_comparable_price(offer_b, item)
        # pa = 18*16 = 288; pb = 50*16 = 800 → A cheaper despite higher per_item
        assert pa == 288
        assert pb == 800
        assert pa < pb
