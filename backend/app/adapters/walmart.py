"""Walmart adapter — grocery rollbacks & deals.

Data acquisition path
--------------------
Walmart does not publish a traditional weekly circular.  Instead they expose
"rollbacks" and deals through their storefront pages.  Walmart's site is
JS-heavy and behind bot detection (Akamai), so direct httpx GETs usually
fail.  We use the browserless container to render the page, falling back
to a raw httpx GET.

1. **Primary — Flipp API** (backflipp.wishabi.com).  Walmart's weekly ad is
   also syndicated on Flipp, keyed by **postal code**.  This is the most
   reliable path (no bot-wall, structured JSON).  We filter flyers by
   ``merchant_name`` matching "walmart".

2. **Fallback — browserless HTML scrape** of the grocery rollback page:
   ``https://www.walmart.com/shop/grocery/rollback``
   (filtered to grocery categories only via keyword matching).

ToS note (spec §5.2): Walmart's ToS prohibits automated scraping.  The
Walmart Affiliate/Developer API (if available) is the sanctioned path.
This adapter is written for completeness; production use should use the
official Walmart Developer API or a licensed data feed.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from .base import AdMetadata, OfferData, StoreAdapter, BrowserClient
from .flipp_mixin import FlippMixin

logger = logging.getLogger(__name__)

WALMART_GROCERY_ROLLBACK_URL = "https://www.walmart.com/shop/grocery/rollback"

# Grocery-category keywords for filtering non-grocery deals out
GROCERY_KEYWORDS = {
    "milk", "bread", "egg", "cheese", "yogurt", "chicken", "beef", "pork",
    "pasta", "rice", "cereal", "soup", "sauce", "snack", "cookie", "chip",
    "fruit", "vegetable", "produce", "frozen", "juice", "soda", "water",
    "coffee", "tea", "beer", "wine", "detergent", "soap", "shampoo",
    "toothpaste", "paper towel", "toilet paper", "diaper", "household",
    "beverage", "food", "grocery", "bakery", "deli", "dairy",
}


class WalmartAdapter(FlippMixin, StoreAdapter):
    STORE_KEY = "walmart"
    STORE_NAME = "Walmart"
    DEFAULT_RATE_LIMIT = 2.0
    FLIPP_MERCHANT_PATTERN = r"walmart"

    def __init__(self, store_id: str = "walmart", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Walmart grocery rollbacks/deals."""
        metadata = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        # Try Flipp first (thread ZIP code)
        try:
            offers, meta = await self.fetch_flipp(self.zip_or_store_id)
            if offers:
                # Filter to grocery categories only
                grocery_offers = [
                    o for o in offers if _is_grocery(o.product_name) or _is_grocery(o.raw_text)
                ]
                if grocery_offers:
                    return grocery_offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("walmart: Flipp path failed: %s", exc)

        # Fallback HTML scrape via browserless
        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("walmart: HTML fallback failed: %s", exc)

        logger.warning("walmart: no offers retrieved")
        return [], metadata

    # --------------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        url = WALMART_GROCERY_ROLLBACK_URL
        if not await self.check_robots_txt(url):
            logger.info("walmart: robots.txt disallows %s", url)
            return [], AdMetadata(store_location=self.zip_or_store_id)

        # Try browserless first for JS-rendered page
        html: str | None = None
        try:
            bc = BrowserClient()
            html = await bc.render_page(url)
        except Exception as exc:
            logger.debug("walmart: browserless unavailable: %s", exc)

        if not html:
            resp = await self.fetch_with_retry(url)
            if resp is None:
                return [], AdMetadata(store_location=self.zip_or_store_id)
            html = resp.text

        self.save_raw_payload({"html": html[:50000]}, suffix="_html")

        offers: list[OfferData] = []
        # Walmart embeds product data in JSON scripts; naive regex parse
        for m in re.finditer(
            r'"title"\s*:\s*"([^"]+)"[^}]*?"price"\s*:\s*"?(\d+\.?\d*)"?', html
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
