"""Matching pipeline unit tests (matching.py).

Covers normalize_offer_text, exclude-keyword hard filters, the confident
vs uncertain classification thresholds, and the brand boost.

These are pure unit tests — no DB required.
"""

from __future__ import annotations

from app.matching import (
    BRAND_BOOST,
    CONFIDENT_THRESHOLD,
    MatchStatus,
    UNCERTAIN_THRESHOLD,
    classify,
    normalize_offer_text,
    score_match,
)
from app.models import Item


# =========================================================================== #
# normalize_offer_text
# =========================================================================== #

class TestNormalizeOfferText:
    def test_lowercases(self):
        assert normalize_offer_text("WHOLE MILK") == "whole milk"

    def test_strips_sizes(self):
        result = normalize_offer_text("Whole Milk 16 oz")
        assert "16" not in result
        assert "oz" not in result
        assert "milk" in result

    def test_strips_punctuation(self):
        result = normalize_offer_text("Milk, 2% — $2.99")
        # punctuation stripped; keep $ . / -
        assert "," not in result
        assert "milk" in result

    def test_expands_abbreviations(self):
        # "pkg" expands to "package"; use it where it isn't a size pattern
        result = normalize_offer_text("pkg of cookies")
        assert "package" in result

    def test_collapses_whitespace(self):
        result = normalize_offer_text("  whole   milk  ")
        assert result == "whole milk"

    def test_empty(self):
        assert normalize_offer_text("") == ""

    def test_dollar_sign_kept(self):
        result = normalize_offer_text("$2.99 milk")
        assert "$" in result


# =========================================================================== #
# Exclude keywords as hard filters
# =========================================================================== #

class TestExcludeKeywords:
    def test_exclude_hard_filter(self):
        """If an exclude keyword appears in the offer, the match is excluded."""
        item = Item(
            name="Chicken Breast",
            match_keywords=["chicken", "breast"],
            exclude_keywords=["thigh", "wing"],
        )
        result = score_match(item, "Boneless Chicken Breast")
        assert result.excluded is False
        assert result.score > 0

        result2 = score_match(item, "Chicken Thigh")
        assert result2.excluded is True
        assert result2.score == 0.0

    def test_exclude_partial_token(self):
        item = Item(
            name="Milk",
            match_keywords=["milk"],
            exclude_keywords=["almond"],
        )
        result = score_match(item, "Almond Milk")
        assert result.excluded is True


# =========================================================================== #
# Confident vs uncertain classification thresholds
# =========================================================================== #

class TestClassifyThresholds:
    def test_confident_at_threshold(self):
        assert classify(CONFIDENT_THRESHOLD) == MatchStatus.confident

    def test_confident_above_threshold(self):
        assert classify(CONFIDENT_THRESHOLD + 1) == MatchStatus.confident

    def test_uncertain_between_thresholds(self):
        assert classify((CONFIDENT_THRESHOLD + UNCERTAIN_THRESHOLD) / 2) == MatchStatus.uncertain

    def test_uncertain_at_lower_threshold(self):
        assert classify(UNCERTAIN_THRESHOLD) == MatchStatus.uncertain

    def test_rejected_below_threshold(self):
        assert classify(UNCERTAIN_THRESHOLD - 1) == MatchStatus.rejected

    def test_threshold_values(self):
        # Spec-aligned constants (sanity check they didn't drift)
        assert CONFIDENT_THRESHOLD == 75.0
        assert UNCERTAIN_THRESHOLD == 50.0
        assert BRAND_BOOST == 15.0


# =========================================================================== #
# Brand boost
# =========================================================================== #

class TestBrandBoost:
    def test_brand_boost_increases_score(self):
        """A preferred brand in the offer adds BRAND_BOOST."""
        item_no_brand = Item(
            name="Greek Yogurt",
            match_keywords=["greek", "yogurt"],
            preferred_brands=[],
        )
        item_with_brand = Item(
            name="Greek Yogurt",
            match_keywords=["greek", "yogurt"],
            preferred_brands=["Chobani"],
        )
        offer_text = "Chobani Greek Yogurt 32 oz"
        score_no = score_match(item_no_brand, offer_text, offer_brand="Chobani")
        score_yes = score_match(item_with_brand, offer_text, offer_brand="Chobani")

        assert score_yes.brand_boosted is True
        assert score_no.brand_boosted is False
        # The boost is exactly BRAND_BOOST (capped at 100).
        assert score_yes.score >= score_no.score
        assert score_yes.score - score_no.score >= BRAND_BOOST - 1  # rounding slack

    def test_no_brand_no_boost(self):
        item = Item(
            name="Greek Yogurt",
            match_keywords=["greek", "yogurt"],
            preferred_brands=["Chobani"],
        )
        result = score_match(item, "Greek Yogurt 32 oz", offer_brand="")
        assert result.brand_boosted is False

    def test_wrong_brand_no_boost(self):
        item = Item(
            name="Greek Yogurt",
            match_keywords=["greek", "yogurt"],
            preferred_brands=["Chobani"],
        )
        result = score_match(item, "Greek Yogurt 32 oz", offer_brand="Fage")
        assert result.brand_boosted is False

    def test_brand_boost_can_push_to_confident(self):
        """An offer just under confident without the brand should reach
        confident when the brand matches."""
        item = Item(
            name="Greek Yogurt",
            match_keywords=["greek", "yogurt"],
            preferred_brands=["Chobani"],
        )
        # Same offer, with and without brand attribution
        no_brand = score_match(item, "Chobani Greek Yogurt", offer_brand="")
        with_brand = score_match(item, "Chobani Greek Yogurt", offer_brand="Chobani")
        if no_brand.score < CONFIDENT_THRESHOLD:
            assert with_brand.score >= CONFIDENT_THRESHOLD
