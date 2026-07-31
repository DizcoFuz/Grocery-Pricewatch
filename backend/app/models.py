"""SQLAlchemy ORM models for the Grocery Pricewatch app.

All monetary values are stored as **integer cents** (spec §5.1).
Uses SQLAlchemy 2.0 declarative style with Mapped / mapped_column.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MatchStatus(str, Enum):
    """Status of an offer→item match."""

    confident = "confident"
    uncertain = "uncertain"
    accepted = "accepted"
    rejected = "rejected"


class MatchDecidedBy(str, Enum):
    """Who decided the match status."""

    auto = "auto"
    user = "user"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class Store(Base):
    """A grocery store chain the app fetches ads from."""

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_key: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    zip_or_store_id: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_fetch_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    ad_cycles: Mapped[list[AdCycle]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Store {self.id} {self.name}>"


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


class Item(Base):
    """A product the user tracks across stores."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    match_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    exclude_keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    preferred_brands: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unit_of_measure: Mapped[str] = mapped_column(String(20), nullable=False, default="ea")
    typical_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    baseline_price_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    matches: Mapped[list[Match]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    match_rules: Mapped[list[MatchRule]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Item {self.id} {self.name}>"


# ---------------------------------------------------------------------------
# AdCycle
# ---------------------------------------------------------------------------


class AdCycle(Base):
    """One weekly ad cycle for a store."""

    __tablename__ = "ad_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    raw_payload_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    store: Mapped[Store] = relationship(back_populates="ad_cycles")
    offers: Mapped[list[Offer]] = relationship(
        back_populates="ad_cycle", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AdCycle {self.id} store={self.store_id} {self.period_start}→{self.period_end}>"


# ---------------------------------------------------------------------------
# Offer
# ---------------------------------------------------------------------------


class Offer(Base):
    """A single sale offer from a store's ad cycle.

    All prices in integer cents.
    """

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_cycle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ad_cycles.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    size_text: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deal_type: Mapped[str] = mapped_column(String(60), nullable=False, default="sale")
    effective_unit_price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_membership_or_coupon: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # P0-4: explicit per-item and per-oz price bases (source of truth for comparisons).
    # `price` remains the headline price (e.g. 299 for "$2.99"; 600 for "2 for $6").
    # `effective_unit_price` is kept for backward compat but comparisons must go through
    # matching.compute_comparable_price() using these two fields.
    price_per_item_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_per_oz_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ad_cycle: Mapped[AdCycle] = relationship(back_populates="offers")
    matches: Mapped[list[Match]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Offer {self.id} {self.product_name!r} price={self.price}c>"


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------


class Match(Base):
    """An offer→item match with confidence score and reviewable status."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("offers.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[MatchStatus] = mapped_column(
        SAEnum(MatchStatus, name="match_status", native_enum=False),
        nullable=False,
        default=MatchStatus.uncertain,
    )
    decided_by: Mapped[MatchDecidedBy] = mapped_column(
        SAEnum(MatchDecidedBy, name="match_decided_by", native_enum=False),
        nullable=False,
        default=MatchDecidedBy.auto,
    )

    offer: Mapped[Offer] = relationship(back_populates="matches")
    item: Mapped[Item] = relationship(back_populates="matches")

    def __repr__(self) -> str:
        return f"<Match {self.id} offer={self.offer_id} item={self.item_id} {self.status}>"


# ---------------------------------------------------------------------------
# PriceHistory
# ---------------------------------------------------------------------------


class PriceHistory(Base):
    """Best unit price observed for an item at a store in a given week.

    Composite primary key on (item_id, store_id, week).
    """

    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("item_id", "store_id", "week", name="uq_price_history"),)

    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    store_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True
    )
    week: Mapped[date] = mapped_column(Date, primary_key=True)
    best_unit_price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deal_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")

    item: Mapped[Item] = relationship(back_populates="price_history")
    store: Mapped[Store] = relationship(back_populates="price_history")

    def __repr__(self) -> str:
        return f"<PriceHistory item={self.item_id} store={self.store_id} week={self.week} {self.best_unit_price}c>"


# ---------------------------------------------------------------------------
# WeeklyReport
# ---------------------------------------------------------------------------


class WeeklyReport(Base):
    """Computed weekly recommendation summary."""

    __tablename__ = "weekly_reports"

    week: Mapped[date] = mapped_column(Date, primary_key=True)
    best_single_store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stores.id", ondelete="SET NULL"), nullable=True
    )
    best_pair_store_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    projected_savings_single: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projected_savings_pair: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_item_results_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    def __repr__(self) -> str:
        return f"<WeeklyReport week={self.week} single={self.projected_savings_single}c pair={self.projected_savings_pair}c>"


# ---------------------------------------------------------------------------
# Setting
# ---------------------------------------------------------------------------


class MatchRule(Base):
    """A user's accepted/rejected decision for an offer text, persisted so the
    same offer text in future cycles auto-applies the decision (FR-3.2).
    """

    __tablename__ = "match_rules"
    __table_args__ = (
        UniqueConstraint("item_id", "normalized_offer_text", name="uq_match_rule"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    normalized_offer_text: Mapped[str] = mapped_column(String(500), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # "accepted" or "rejected"
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    item: Mapped[Item] = relationship(back_populates="match_rules")

    def __repr__(self) -> str:
        return f"<MatchRule item={self.item_id} decision={self.decision} text={self.normalized_offer_text!r}>"


class Setting(Base):
    """Application setting (key-value store)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<Setting {self.key}={self.value!r}>"
