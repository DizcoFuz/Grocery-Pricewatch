"""Walmart adapter — grocery rollbacks & deals.

Data acquisition path
--------------------
Walmart does not publish a traditional weekly circular.  Instead they expose
"rollbacks" and deals through their storefront APIs.

1. **Primary** — Walmart grocery rollback/deals API.  The endpoint shape is::

       https://www.walmart.com/grocery/v2/api/deals
       https://www.walmart.com/store/electronics/deals

   In practice the exact internal API changes frequently and is gated by
   bot detection (Akamai).  We attempt a known rollback endpoint and fall
   back to the deals page HTML.

2. **Fallback** — HTML scrape of the deals page, filtering to grocery
   categories only (food, beverage, household essentials) by keyword
   matching on product names / category breadcrumbs.

ToS note (spec §5.2): Walmart's ToS prohibits automated scraping.  The
Walmart Affiliate/Developer API (if available) is the sanctioned path.
This adapter is written for completeness; production use should use the
official Walmart Developer API or a licensed data feed.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

WALMART_DEALS_URL = "https://www.walmart.com/store/electronics/deals"
WALMART_GROCERY_API = "https://www.walmart.com/grocery/v2/api/deals"

# Grocery-category keywords for filtering non-grocery deals out
GROCERY_KEYWORDS = {
    "milk", "bread", "egg", "cheese", "yogurt", "chicken", "beef", "pork",
    "pasta", "rice", "cereal", "soup", "sauce", "snack", "cookie", "chip",
    "fruit", "vegetable", "produce", "frozen", "juice", "soda", "water",
    "coffee", "tea", "beer", "wine", "detergent", "soap", "shampoo",
    "toothpaste", "paper towel", "toilet paper", "diaper", "household",
    "beverage", "food", "grocery", "bakery", "deli", "dairy",
}


class WalmartAdapter(StoreAdapter):
    STORE_KEY = "walmart"
    STORE_NAME = "Walmart"
    DEFAULT_RATE_LIMIT = 2.0

    def __init__(self, store_id: str = "walmart", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Walmart grocery rollbacks/deals."""
        metadata = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        # Try API first
        try:
            offers, meta = await self._fetch_via_api()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("walmart: API path failed: %s", exc)

        # Fallback HTML scrape
        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("walmart: HTML fallback failed: %s", exc)

        logger.warning("walmart: no offers retrieved")
        return [], metadata

    # --------------------------------------------------------------- API

    async def _fetch_via_api(self) -> tuple[list[OfferData], AdMetadata]:
        params = {"category": "grocery", "zip": self.zip_or_store_id}
        resp = await self.fetch_with_retry(WALMART_GROCERY_API, params=params)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)

        try:
            data = resp.json()
        except ValueError:
            return [], AdMetadata(store_location=self.zip_or_store_id)

        self.save_raw_payload(data, suffix="_api")
        return self._parse_api(data)

    def _parse_api(self, data: dict[str, Any]) -> tuple[list[OfferData], AdMetadata]:
        offers: list[OfferData] = []
        items = data.get("items") or data.get("products") or data.get("deals") or []
        if isinstance(items, dict):
            items = list(items.values())

        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or item.get("title") or "").strip()
            if not name or not _is_grocery(name):
                continue
            price_raw = item.get("price") or item.get("salePrice") or item.get(
                "rollbackPrice"
            )
            price = self.dollars_to_cents(price_raw)
            if not price:
                continue
            size_text = (item.get("size") or item.get("packageSize") or "").strip()
            brand = (item.get("brand") or "").strip()
            deal_type = "rollback" if item.get("rollbackPrice") else "sale"
            offers.append(
                OfferData(
                    raw_text=f"{name} ${price_raw}",
                    product_name=name,
                    brand=brand,
                    size_text=size_text,
                    price=price,
                    deal_type=deal_type,
                )
            )

        meta = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )
        return offers, meta

    # --------------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        resp = await self.fetch_with_retry(WALMART_DEALS_URL)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload({"html": resp.text[:50000]}, suffix="_html")

        offers: list[OfferData] = []
        # Walmart embeds product data in JSON scripts; naive regex parse
        for m in re.finditer(
            r'"title"\s*:\s*"([^"]+)"[^}]*?"price"\s*:\s*"?(\d+\.?\d*)"?', resp.text
        ):
            name = m.group(1).strip()
            if not _is_grocery(name):
                continue
            price = self.dollars_to_cents(m.group(2))
            if price:
                offers.append(
                    OfferData(
                        raw_text=f"{name} ${m.group(2)}",
                        product_name=name,
                        price=price,
                        deal_type="rollback",
                    )
                )
        meta = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )
        return offers, meta


# ------------------------------------------------------------------ helpers


def _is_grocery(name: str) -> bool:
    """Heuristic: does ``name`` look like a grocery/household-essential item?"""
    low = name.lower()
    return any(kw in low for kw in GROCERY_KEYWORDS)
