"""Pydantic v2 schemas for the Grocery Pricewatch API."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class ORMModel(BaseModel):
    """Base with ORM-mode configured."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class MatchStatusEnum(str, Enum):
    confident = "confident"
    uncertain = "uncertain"
    accepted = "accepted"
    rejected = "rejected"


class MatchDecidedByEnum(str, Enum):
    auto = "auto"
    user = "user"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StoreBase(BaseModel):
    name: str = Field(..., max_length=120)
    adapter_key: str = Field(..., max_length=60)
    zip_or_store_id: str = Field("", max_length=40)
    enabled: bool = True


class StoreCreate(StoreBase):
    pass


class StoreUpdate(BaseModel):
    name: str | None = None
    adapter_key: str | None = None
    zip_or_store_id: str | None = None
    enabled: bool | None = None


class StoreRead(StoreBase, ORMModel):
    id: int
    last_fetch_at: datetime | None = None
    last_fetch_status: str | None = None


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


class ItemBase(BaseModel):
    name: str = Field(..., max_length=200)
    category: str = Field("", max_length=100)
    match_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    unit_of_measure: str = Field("ea", max_length=20)
    typical_quantity: float = Field(1.0, ge=0.01)
    baseline_price_override: int | None = Field(None, description="Price in cents")
    active: bool = True


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    match_keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    preferred_brands: list[str] | None = None
    unit_of_measure: str | None = None
    typical_quantity: float | None = None
    baseline_price_override: int | None = None
    active: bool | None = None


class ItemRead(ItemBase, ORMModel):
    id: int
    created_at: datetime


# ---------------------------------------------------------------------------
# CSV / JSON import row
# ---------------------------------------------------------------------------


class ItemImportRow(BaseModel):
    """One row from a CSV/JSON item import.

    The importer will deduplicate by case-insensitive ``name``.
    """

    name: str = Field(..., max_length=200)
    category: str = ""
    match_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    unit_of_measure: str = "ea"
    typical_quantity: float = 1.0
    baseline_price_override: int | None = None
    active: bool = True

    model_config = ConfigDict(extra="ignore")


class ItemImportResult(BaseModel):
    """Summary of an import operation."""

    total_rows: int
    imported: int
    skipped_duplicates: int
    errors: list[str] = Field(default_factory=list)
    preview: list[ItemRead] = Field(default_factory=list)


class ItemTemplate(BaseModel):
    """Empty template row with field hints."""

    name: str = ""
    category: str = ""
    match_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    unit_of_measure: str = "ea"
    typical_quantity: float = 1.0
    baseline_price_override: int | None = None
    active: bool = True


# ---------------------------------------------------------------------------
# AdCycle
# ---------------------------------------------------------------------------


class AdCycleRead(ORMModel):
    id: int
    store_id: int
    period_start: date
    period_end: date
    fetched_at: datetime
    raw_payload_ref: str


# ---------------------------------------------------------------------------
# Offer
# ---------------------------------------------------------------------------


class OfferRead(ORMModel):
    id: int
    ad_cycle_id: int
    raw_text: str
    product_name: str
    brand: str
    size_text: str
    price: int
    deal_type: str
    effective_unit_price: int
    unit_price_unknown: bool
    requires_membership_or_coupon: bool
    # P0-4: explicit per-item and per-oz price bases (source of truth).
    price_per_item_cents: int | None = None
    price_per_oz_cents: int | None = None


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------


class MatchRead(ORMModel):
    id: int
    offer_id: int
    item_id: int
    confidence: float
    status: MatchStatusEnum
    decided_by: MatchDecidedByEnum


class MatchReview(BaseModel):
    """User decision on an uncertain match."""

    decision: str = Field(..., pattern="^(accept|reject)$")


class MatchWithDetails(MatchRead):
    """Match enriched with offer + item info for the review queue."""

    offer: OfferRead
    item_name: str
    item_id: int
    store_name: str


# ---------------------------------------------------------------------------
# PriceHistory
# ---------------------------------------------------------------------------


class PriceHistoryRead(ORMModel):
    item_id: int
    store_id: int
    week: date
    best_unit_price: int
    deal_type: str


# ---------------------------------------------------------------------------
# WeeklyReport
# ---------------------------------------------------------------------------


class WeeklyReportRead(ORMModel):
    week: date
    best_single_store_id: int | None
    best_pair_store_ids: list[int]
    projected_savings_single: int
    projected_savings_pair: int
    per_item_results_json: str


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingRead(ORMModel):
    key: str
    value: str


class SettingUpdate(BaseModel):
    key: str
    value: str


class SettingsBundle(BaseModel):
    """All settings in one response."""

    two_store_threshold: int = 500
    baseline_strategy: str = "auto"
    refresh_schedule: str = "07:00"


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class StoreCostDetail(BaseModel):
    """Per-item cost within a store recommendation."""

    item_id: int
    item_name: str
    unit_price: int
    quantity: float
    line_total: int
    is_sale: bool
    deal_type: str = ""


class SingleStoreRecommendation(BaseModel):
    store_id: int
    store_name: str
    total_cost: int
    baseline_cost: int
    savings: int
    item_count: int
    details: list[StoreCostDetail]


class TwoStoreRecommendation(BaseModel):
    store_ids: list[int]
    store_names: list[str]
    total_cost: int
    baseline_cost: int
    savings: int
    marginal_benefit: int
    item_count: int
    details: list[StoreCostDetail]
    item_store_map: dict[int, int]


class RecommendationsResponse(BaseModel):
    single: list[SingleStoreRecommendation]
    best_single: SingleStoreRecommendation | None
    two_store: list[TwoStoreRecommendation]
    best_pair: TwoStoreRecommendation | None
    two_store_threshold: int
    potential_savings_pending_review: int = 0


# ---------------------------------------------------------------------------
# Best prices (P0-3: current vs. last best — headline feature)
# ---------------------------------------------------------------------------


class BestPriceEntry(BaseModel):
    """Per-item best-price row for the best-prices table (FR-4.2/4.3)."""

    item_id: int
    item_name: str
    category: str = ""
    current_best_price: int | None = None  # cents
    current_best_store_id: int | None = None
    current_best_store_name: str = ""
    current_best_deal_type: str = ""
    last_best_price: int | None = None  # cents
    last_best_store_name: str = ""
    last_best_week: str = ""  # ISO date
    delta_cents: int | None = None  # negative = cheaper (better)
    delta_direction: str = ""  # "better" | "worse" | "unchanged" | "new"
    all_time_best_price: int | None = None
    all_time_best_store_name: str = ""
    all_time_best_week: str = ""
    other_store_prices: list[dict] = []  # [{store_name, price, deal_type, unit_price_unknown}]
    unit_price_unknown: bool = False


class BestPricesResponse(BaseModel):
    items_with_deals: list[BestPriceEntry]
    items_without_deals: list[BestPriceEntry]  # "No deals this week" section (FR-4.3)


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------


class SavingsResponse(BaseModel):
    weekly_savings: int
    cumulative_savings: int
    weekly_report: WeeklyReportRead | None
    history: list[WeeklyReportRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class StoreStatus(BaseModel):
    store_id: int
    name: str
    enabled: bool
    last_fetch_at: datetime | None
    last_fetch_status: str | None


class DashboardBestDeal(BaseModel):
    item_name: str
    store_name: str
    sale_price: int
    unit_price: int
    deal_type: str
    savings_vs_baseline: int


class DashboardResponse(BaseModel):
    headline_savings: int
    headline_store: str
    headline_mode: str
    best_deals: list[DashboardBestDeal]
    store_statuses: list[StoreStatus]
    review_queue_count: int
    last_report: WeeklyReportRead | None
    banner: str | None = None
    # P0-3: best-prices data for the best-deals table (current vs. last best).
    best_prices: BestPricesResponse | None = None


# ---------------------------------------------------------------------------
# Shopping list
# ---------------------------------------------------------------------------


class ShoppingListEntry(BaseModel):
    item_id: int
    item_name: str
    quantity: float
    unit_of_measure: str
    store_id: int
    store_name: str
    unit_price: int
    line_total: int
    is_sale: bool
    deal_type: str = ""


class ShoppingListResponse(BaseModel):
    mode: str
    total_cost: int
    baseline_cost: int
    savings: int
    store_ids: list[int]
    store_names: list[str]
    entries: list[ShoppingListEntry]


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class StoreRefreshResult(BaseModel):
    store_id: int
    store_name: str
    status: str
    offers_fetched: int = 0
    matches_created: int = 0
    error: str | None = None


class RefreshAllResult(BaseModel):
    results: list[StoreRefreshResult]
    total_offers: int
    total_matches: int
    weekly_report: WeeklyReportRead | None = None
