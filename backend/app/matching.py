"""Offer-to-item matching pipeline and price normalization.

Pipeline steps (spec §5):
1. normalize_offer_text — lowercase, strip sizes/punctuation, expand abbreviations
2. normalize_price — handle multi-buy, BOGO, compute effective_unit_price, flag deal_type
3. score_match — keyword + fuzzy (rapidfuzz) + preferred-brand boost
4. classify — confident (>= threshold), uncertain, or no match
5. process_matches — run all active items vs all offers, create Match records

All price math in integer cents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import AdCycle, Item, Match, MatchDecidedBy, MatchStatus, Offer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIDENT_THRESHOLD = 75.0  # >= this → confident
UNCERTAIN_THRESHOLD = 50.0  # >= this but < confident → uncertain; below → no match
BRAND_BOOST = 15.0
KEYWORD_WEIGHT = 40.0
FUZZY_WEIGHT = 60.0

# Common abbreviation expansion map
ABBREVIATIONS: dict[str, str] = {
    "lb": "pound",
    "lbs": "pound",
    "oz": "ounce",
    "ozs": "ounce",
    "fl oz": "fluid ounce",
    "pk": "pack",
    "pkg": "package",
    "ct": "count",
    "ea": "each",
    "qt": "quart",
    "pt": "pint",
    "gal": "gallon",
    "gr": "gram",
    "g": "gram",
    "kg": "kilogram",
    "ml": "milliliter",
    "l": "liter",
    "w/": "with",
    "w/o": "without",
    "boGo": "buy one get one",
    "b1g1": "buy one get one",
}

# Regex patterns
_SIZE_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:lb|lbs|oz|ozs|fl\s*oz|ml|l|g|kg|gr|pk|pkg|ct|ea|qt|pt|gal|cup|cups|gallon|ounce|pound|gram|pack|count|each|quart|pint)\b",
    re.IGNORECASE,
)
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s$%./-]")
_MULTI_BUY_PATTERN = re.compile(
    r"(\d+)\s*(?:for|/)\s*\$?(\d+(?:\.\d{1,2})?)", re.IGNORECASE
)
_BOGO_PATTERN = re.compile(
    r"\b(?:bogo|b1g1|buy\s+one\s+get\s+one|buy\s+1\s+get\s+1|1\s+for\s+1)\b",
    re.IGNORECASE,
)
_PRICE_PATTERN = re.compile(r"\$?(\d+(?:\.\d{1,2})?)")
_EA_PRICE_PATTERN = re.compile(r"(?:ea|each)\s*[:\.]?\s*\$?(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
_OR_PATTERN = re.compile(r"\$\d+(?:\.\d{1,2})?\s*(?:or|/)\s*\$?(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
_MEMBERSHIP_PATTERN = re.compile(
    r"\b(?:prime|member|membership|club|rewards?|coupon|mfr\s*coupon|store\s*coupon|card)\b",
    re.IGNORECASE,
)
_QUANTITY_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*(lb|oz|fl\s*oz|ml|l|g|kg|gr|pk|pkg|ct|ea|qt|pt|gal|cup|gallon|ounce|pound|gram|pack|count|each|quart|pint)$", re.IGNORECASE)

# Unit conversion to "per oz" for unit price comparison (oz as common unit)
_UNIT_TO_OZ: dict[str, float] = {
    "oz": 1.0,
    "ounce": 1.0,
    "ozs": 1.0,
    "lb": 16.0,
    "lbs": 16.0,
    "pound": 16.0,
    "g": 0.035274,
    "gram": 0.035274,
    "gr": 0.035274,
    "kg": 35.274,
    "fl oz": 1.0,
    "fluid ounce": 1.0,
    "ml": 0.033814,
    "l": 33.814,
    "liter": 33.814,
    "qt": 32.0,
    "quart": 32.0,
    "pt": 16.0,
    "pint": 16.0,
    "gal": 128.0,
    "gallon": 128.0,
    "cup": 8.0,
    "cups": 8.0,
    "ea": 1.0,
    "each": 1.0,
    "ct": 1.0,
    "count": 1.0,
    "pk": 1.0,
    "pack": 1.0,
    "pkg": 1.0,
    "package": 1.0,
}


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------


def _expand_abbreviations(text: str) -> str:
    """Expand common grocery abbreviations to their full forms."""
    result = text
    for abbr, full in sorted(ABBREVIATIONS.items(), key=lambda x: -len(x[0])):
        result = re.sub(rf"\b{re.escape(abbr)}\b", full, result, flags=re.IGNORECASE)
    return result


def normalize_offer_text(text: str) -> str:
    """Normalize an offer's raw text for matching.

    - lowercase
    - strip size descriptors (lb, oz, pk, etc.)
    - strip punctuation (keep $ % . / -)
    - expand common abbreviations
    - collapse whitespace
    """
    if not text:
        return ""
    # lowercase
    result = text.lower().strip()
    # expand abbreviations before stripping sizes so we catch expanded forms too
    result = _expand_abbreviations(result)
    # strip size patterns
    result = _SIZE_PATTERN.sub(" ", result)
    # strip punctuation
    result = _PUNCTUATION_PATTERN.sub(" ", result)
    # collapse whitespace
    result = re.sub(r"\s+", " ", result).strip()
    return result


def extract_brand(text: str) -> str:
    """Attempt to extract a brand name from offer text.

    Heuristic: the first 1-2 capitalized words before a product noun.
    Falls back to empty string if unclear.
    """
    if not text:
        return ""
    # Try to find patterns like "Brand Name Product"
    words = text.strip().split()
    if not words:
        return ""
    # Common product nouns that signal the product part starts
    product_nouns = {
        "milk", "bread", "cheese", "yogurt", "butter", "eggs", "chicken",
        "beef", "pork", "bacon", "ham", "turkey", "rice", "pasta", "sauce",
        "cereal", "coffee", "tea", "juice", "soda", "water", "chips",
        "crackers", "cookies", "cake", "mix", "flour", "sugar", "oil",
        "vinegar", "soup", "broth", "beans", "corn", "peas", "broccoli",
        "lettuce", "tomato", "tomatoes", "onion", "onions", "potato",
        "potatoes", "apple", "apples", "banana", "bananas", "orange",
        "oranges", "grapes", "berries", "strawberry", "blueberry",
    }
    brand_words: list[str] = []
    for w in words:
        wl = w.lower().strip(".,;:!?")
        if wl in product_nouns:
            break
        # Only treat as brand if it looks like a proper noun (Capitalized) or all-caps
        if w[0].isupper() or w.isupper():
            brand_words.append(w)
        else:
            break
        if len(brand_words) >= 2:
            break
    return " ".join(brand_words)


# ---------------------------------------------------------------------------
# Price normalization (all in integer cents)
# ---------------------------------------------------------------------------


@dataclass
class NormalizedPrice:
    """Result of parsing an offer's price text.

    P0-4: the two source-of-truth bases are price_per_item_cents and
    price_per_oz_cents. ``effective_unit_price`` is kept for backward
    compatibility but is no longer used for offer-to-offer comparison —
    use compute_comparable_price() for that.
    """

    price: int = 0  # headline price in cents
    deal_type: str = "sale"
    effective_unit_price: int = 0  # legacy per-unit price (best-effort, kept for back-compat)
    unit_price_unknown: bool = True
    requires_membership_or_coupon: bool = False
    quantity: float = 1.0  # number of units the headline price buys
    # New explicit bases (may be None when not derivable).
    price_per_item_cents: int | None = None
    price_per_oz_cents: int | None = None


def _dollars_to_cents(dollar_str: str) -> int:
    """Convert a dollar string like '4.99' to integer cents (499)."""
    try:
        return int(round(float(dollar_str) * 100))
    except (ValueError, TypeError):
        return 0


def _parse_size_text(size_text: str) -> tuple[float, str] | None:
    """Parse a size string like '16 oz', '2 lb', '12 pack' → (quantity, unit).

    Returns (quantity, unit_lowercased) or None if unparseable.
    """
    if not size_text:
        return None
    size_text = size_text.strip().lower()
    m = re.match(r"(\d+(?:\.\d+)?)\s*(.+)", size_text)
    if not m:
        return None
    try:
        qty = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2).strip()
    return (qty, unit)


def _total_oz_from_size(size_text: str, quantity: float = 1.0) -> float | None:
    """Return total ounces described by *size_text* times *quantity*.

    ``size_text`` like "32 oz" with quantity 2 (multi-buy) → 64 oz.
    Returns None if the unit isn't convertible to ounces (e.g. "ea", "ct").
    """
    size_info = _parse_size_text(size_text)
    if size_info is None:
        return None
    size_qty, size_unit = size_info
    factor = _UNIT_TO_OZ.get(size_unit.lower())
    if factor is None or factor <= 0:
        return None
    return size_qty * factor * quantity


_PER_LB_PATTERN = re.compile(r"/\s*lb\b|per\s+lb\b|per\s+pound\b|/\s*pound\b", re.IGNORECASE)


def normalize_price(
    raw_text: str,
    *,
    size_text: str = "",
    unit_of_measure: str = "ea",
) -> NormalizedPrice:
    """Parse an offer's price text into a normalized price object.

    Computes two explicit bases (P0-4):
      * ``price_per_item_cents`` — the per-item price when it is derivable
        (simple sale, multi-buy, BOGO). None when unknown (e.g. per-lb
        offer with no item count).
      * ``price_per_oz_cents`` — the per-ounce price when the size is known
        and convertible to ounces. None when unknown.

    All prices returned in integer cents.
    """
    result = NormalizedPrice()
    if not raw_text:
        return result

    text = raw_text.strip()
    result.requires_membership_or_coupon = bool(_MEMBERSHIP_PATTERN.search(text))

    # --- BOGO detection (buy 1 get 1 free) ---
    if _BOGO_PATTERN.search(text):
        price_match = _PRICE_PATTERN.search(text)
        if price_match:
            price_cents = _dollars_to_cents(price_match.group(1))
            result.price = price_cents
            result.deal_type = "bogo"
            result.quantity = 2.0
            # Pay for 1, get 2 → per-item price is half the headline price.
            result.price_per_item_cents = int(round(price_cents / 2))
            # per-oz is derivable only if we know the size of one item.
            total_oz = _total_oz_from_size(size_text, quantity=1.0)
            if total_oz and total_oz > 0:
                # BOGO: 2 items for the price of 1 → total oz = 2 * item_oz.
                result.price_per_oz_cents = int(round(price_cents / (total_oz * 2)))
            result.effective_unit_price = result.price_per_item_cents
            result.unit_price_unknown = result.price_per_oz_cents is None
            return result
        # BOGO with no explicit price — mark unknown
        result.deal_type = "bogo"
        result.unit_price_unknown = True
        return result

    # --- Per-lb pricing ("$2.99/lb", "$2.99 per pound") ---
    if _PER_LB_PATTERN.search(text):
        price_match = _PRICE_PATTERN.search(text)
        if price_match:
            price_cents = _dollars_to_cents(price_match.group(1))
            result.price = price_cents
            result.deal_type = "per_lb"
            result.quantity = 1.0
            # $X/lb → price_per_oz = X/16. price_per_item is unknown (no weight).
            result.price_per_oz_cents = int(round(price_cents / 16))
            result.price_per_item_cents = None
            result.effective_unit_price = result.price_per_oz_cents
            result.unit_price_unknown = False
            return result

    # --- Multi-buy detection (e.g., "2 for $5", "3/$10") ---
    multi_match = _MULTI_BUY_PATTERN.search(text)
    if multi_match:
        qty = int(multi_match.group(1))
        total_cents = _dollars_to_cents(multi_match.group(2))
        result.price = total_cents
        result.deal_type = "multi_buy"
        result.quantity = float(qty)
        # Per-item price = total / qty.
        per_item = total_cents / qty if qty > 0 else 0
        result.price_per_item_cents = int(round(per_item))
        # Per-oz: total_cents / (total_oz across ALL qty items).
        # P0-4 defect 2 fix: divide by total quantity per item × qty, not by the unit factor.
        if size_text:
            total_oz = _total_oz_from_size(size_text, quantity=float(qty))
            if total_oz and total_oz > 0:
                result.price_per_oz_cents = int(round(total_cents / total_oz))
        result.effective_unit_price = result.price_per_item_cents
        result.unit_price_unknown = result.price_per_oz_cents is None
        return result

    # --- Simple price ("$2.99") ---
    price_match = _PRICE_PATTERN.search(text)
    if price_match:
        price_cents = _dollars_to_cents(price_match.group(1))
        result.price = price_cents
        result.deal_type = "sale"
        result.quantity = 1.0
        # Per-item price = headline price (one item).
        result.price_per_item_cents = price_cents
        # Per-oz derivable from size_text (e.g. "$2.99" + "32 oz" → 299/32).
        if size_text:
            total_oz = _total_oz_from_size(size_text, quantity=1.0)
            if total_oz and total_oz > 0:
                result.price_per_oz_cents = int(round(price_cents / total_oz))
        result.effective_unit_price = price_cents
        result.unit_price_unknown = result.price_per_oz_cents is None
        return result

    # --- No price found ---
    result.deal_type = "unknown"
    result.unit_price_unknown = True
    return result


# ---------------------------------------------------------------------------
# Comparable price for offer-vs-item / offer-vs-offer comparison (P0-4)
# ---------------------------------------------------------------------------


# UoM groups
_WEIGHT_UOMS = {"lb", "lbs", "pound", "oz", "ozs", "ounce", "g", "gram", "gr", "kg"}
_COUNT_UOMS = {"ea", "each", "ct", "count", "pk", "pack", "pkg", "package", "dozen"}


def _uom_is_weight(uom: str) -> bool:
    return (uom or "").strip().lower() in _WEIGHT_UOMS


def _uom_is_count(uom: str) -> bool:
    return (uom or "").strip().lower() in _COUNT_UOMS


def compute_comparable_price(offer: Offer, item: Item) -> tuple[int, bool]:
    """Return (comparable_price_cents, is_approximate) for *offer* in the
    item's unit-of-measure basis.

    Rules (P0-4 / FR-3.3):
      * If item.unit_of_measure is a weight (lb/oz/g/kg): use price_per_oz_cents,
        converting to the item's UoM (lb → multiply by 16; oz → as-is).
      * If item.unit_of_measure is a count (ea/ct/pk/dozen): use price_per_item_cents.
      * If the needed basis is null on the offer: fall back to the raw headline
        ``price`` and mark is_approximate=True.
    """
    uom = (item.unit_of_measure or "ea").strip().lower()

    if _uom_is_weight(uom):
        if offer.price_per_oz_cents is not None:
            per_oz = offer.price_per_oz_cents
            if uom in ("lb", "lbs", "pound"):
                return per_oz * 16, False
            # oz and other weight units: per-oz as-is
            return per_oz, False
        # Fall back to raw price — approximate
        return offer.price, True

    if _uom_is_count(uom):
        if offer.price_per_item_cents is not None:
            return offer.price_per_item_cents, False
        return offer.price, True

    # Unknown UoM: fall back to raw price, approximate.
    return offer.price, True


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class MatchScore:
    """Result of scoring an offer against an item."""

    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    excluded: bool = False
    brand_boosted: bool = False


def _tokenize(text: str) -> set[str]:
    """Split text into a set of lowercase word tokens."""
    return {w for w in re.split(r"\s+", text.lower().strip()) if w}


def _fuzzy_ratio(a: str, b: str) -> float:
    """Return a 0-100 fuzzy similarity score using rapidfuzz if available, else difflib."""
    if not a or not b:
        return 0.0
    try:
        from rapidfuzz import fuzz

        return float(fuzz.token_sort_ratio(a, b))
    except ImportError:
        import difflib

        return float(difflib.SequenceMatcher(None, a, b).ratio() * 100)


def score_match(item: Item, offer_text: str, offer_brand: str = "") -> MatchScore:
    """Score an offer's normalized text against an item.

    Scoring:
    - Keyword match: each matched keyword contributes up to KEYWORD_WEIGHT
    - Fuzzy similarity: up to FUZZY_WEIGHT
    - Brand boost: +BRAND_BOOST if offer brand is in preferred_brands
    - Exclude keywords: hard filter → score 0, excluded=True
    """
    norm_offer = normalize_offer_text(offer_text)
    norm_offer_tokens = _tokenize(norm_offer)

    result = MatchScore()

    # --- Exclude check (hard filter) ---
    if item.exclude_keywords:
        for excl in item.exclude_keywords:
            excl_norm = normalize_offer_text(excl)
            if excl_norm and (excl_norm in norm_offer or any(
                t in norm_offer_tokens for t in _tokenize(excl_norm)
            )):
                result.excluded = True
                result.score = 0.0
                return result

    # --- Keyword scoring ---
    keywords = item.match_keywords or []
    # If no explicit match_keywords, use the item name itself
    if not keywords:
        keywords = [item.name]

    keyword_scores: list[float] = []
    matched_keywords: list[str] = []

    for kw in keywords:
        kw_norm = normalize_offer_text(kw)
        if not kw_norm:
            continue
        kw_tokens = _tokenize(kw_norm)
        if not kw_tokens:
            continue
        # Count how many keyword tokens appear in the offer
        matched = kw_tokens & norm_offer_tokens
        if matched:
            frac = len(matched) / len(kw_tokens)
            keyword_scores.append(frac * KEYWORD_WEIGHT)
            matched_keywords.append(kw)
        else:
            # Fuzzy fallback for this keyword
            fuzzy = _fuzzy_ratio(kw_norm, norm_offer)
            if fuzzy >= 70.0:
                keyword_scores.append((fuzzy / 100.0) * KEYWORD_WEIGHT)
                matched_keywords.append(kw)

    keyword_total = min(sum(keyword_scores), KEYWORD_WEIGHT) if keyword_scores else 0.0
    result.matched_keywords = matched_keywords

    # --- Fuzzy name match ---
    item_name_norm = normalize_offer_text(item.name)
    fuzzy_score = _fuzzy_ratio(item_name_norm, norm_offer)
    fuzzy_total = (fuzzy_score / 100.0) * FUZZY_WEIGHT

    # --- Brand boost ---
    if item.preferred_brands and offer_brand:
        offer_brand_lower = offer_brand.lower().strip()
        for brand in item.preferred_brands:
            if brand.lower().strip() in offer_brand_lower:
                result.brand_boosted = True
                break

    # Combine
    base = keyword_total + fuzzy_total
    result.score = base + (BRAND_BOOST if result.brand_boosted else 0.0)

    # Cap at 100
    result.score = min(result.score, 100.0)
    return result


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(score: float) -> MatchStatus:
    """Classify a match score into confident, uncertain, or (implicit) no match."""
    if score >= CONFIDENT_THRESHOLD:
        return MatchStatus.confident
    if score >= UNCERTAIN_THRESHOLD:
        return MatchStatus.uncertain
    return MatchStatus.rejected  # below threshold → no match (rejected by auto)


# ---------------------------------------------------------------------------
# Process matches
# ---------------------------------------------------------------------------


def process_matches(
    db: Session,
    ad_cycle_id: int,
    *,
    confident_threshold: float = CONFIDENT_THRESHOLD,
    uncertain_threshold: float = UNCERTAIN_THRESHOLD,
) -> int:
    """Run all active items against all offers in an ad cycle.

    Creates Match records for confident + uncertain matches.
    Before scoring, consults persisted MatchRules (FR-3.2): an "accepted" rule
    creates an accepted match at confidence 1.0; a "rejected" rule skips the
    offer for that item entirely.
    Returns the number of matches created.
    """
    from app import crud

    offers = (
        db.query(Offer)
        .filter(Offer.ad_cycle_id == ad_cycle_id)
        .all()
    )
    items = db.query(Item).filter(Item.active.is_(True)).all()

    created = 0

    # Clear existing auto matches for this cycle's offers before re-matching
    offer_ids = [o.id for o in offers]
    if offer_ids:
        db.query(Match).filter(
            Match.offer_id.in_(offer_ids),
            Match.decided_by == MatchDecidedBy.auto,
        ).delete(synchronize_session="fetch")

    for offer in offers:
        offer_text = offer.raw_text or offer.product_name
        normalized_offer = normalize_offer_text(offer_text)
        for item in items:
            # --- MatchRule check (FR-3.2) ---
            if normalized_offer:
                rule = crud.get_match_rule(db, item.id, normalized_offer)
                if rule is not None:
                    if rule.decision == "accepted":
                        match = Match(
                            offer_id=offer.id,
                            item_id=item.id,
                            confidence=1.0,
                            status=MatchStatus.accepted,
                            decided_by=MatchDecidedBy.user,
                        )
                        db.add(match)
                        created += 1
                        continue
                    elif rule.decision == "rejected":
                        # Skip this offer for this item entirely.
                        continue

            score_result = score_match(item, offer_text, offer.brand)

            if score_result.excluded:
                continue

            if score_result.score < uncertain_threshold:
                continue

            status = classify(score_result.score)
            # Only create confident or uncertain
            if status not in (MatchStatus.confident, MatchStatus.uncertain):
                continue

            match = Match(
                offer_id=offer.id,
                item_id=item.id,
                confidence=round(score_result.score, 2),
                status=status,
                decided_by=MatchDecidedBy.auto,
            )
            db.add(match)
            created += 1

    db.flush()
    return created


# ---------------------------------------------------------------------------
# Helpers for offer creation
# ---------------------------------------------------------------------------


def build_offer(
    ad_cycle_id: int,
    raw_text: str,
    product_name: str = "",
    brand: str = "",
    size_text: str = "",
) -> Offer:
    """Build an Offer ORM object from raw text, normalizing the price."""
    np = normalize_price(raw_text, size_text=size_text)
    return Offer(
        ad_cycle_id=ad_cycle_id,
        raw_text=raw_text,
        product_name=product_name or normalize_offer_text(raw_text),
        brand=brand or extract_brand(raw_text),
        size_text=size_text,
        price=np.price,
        deal_type=np.deal_type,
        effective_unit_price=np.effective_unit_price,
        unit_price_unknown=np.unit_price_unknown,
        requires_membership_or_coupon=np.requires_membership_or_coupon,
        # P0-4: explicit per-item and per-oz price bases (source of truth).
        price_per_item_cents=np.price_per_item_cents,
        price_per_oz_cents=np.price_per_oz_cents,
    )
