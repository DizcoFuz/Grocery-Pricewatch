"""Target adapter — weekly ad + Circle offers.

Data acquisition path
---------------------
Target exposes:
  * A weekly ad at https://www.target.com/weeklyad
  * Target Circle offers (formerly Cartwheel) — the RedCircle API.

1. **Primary — Target weekly ad API.** Target's weekly ad is served via an
   internal JSON API.  The endpoint shape is::

       https://www.target.com/weeklyad

   In practice this returns an HTML page with embedded JSON.  We also try
   the RedCircle / Cartwheel API for Circle offers.

2. **Fallback — HTML scrape** of the weekly ad page.

Circle offers require the user to "clip" them in the Target app, so we set
``requires_membership_or_coupon=True`` on those offers with ``deal_type="circle"``.

ToS note (spec §5.2): Target's ToS restricts automated access.  The
sanctioned path is the Target Partners API (if granted).  This adapter is
written for completeness.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

TARGET_WEEKLY_AD = "https://www.target.com/weeklyad"
TARGET_CIRCLE_API = "https://www.target.com/redcircle/api/offers"
# Alternative: Target's content API
TARGET_CONTENT_API = "https://redsky.target.com/v2/ffs/email/subscriptions"


class TargetAdapter(StoreAdapter):
    STORE_KEY = "target"
    STORE_NAME = "Target"
    DEFAULT_RATE_LIMIT = 2.0

    def __init__(self, store_id: str = "target", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Target weekly ad items + Circle offers."""
        metadata = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        all_offers: list[OfferData] = []

        # Weekly ad
        try:
            weekly_offers = await self._fetch_weekly_ad()
            all_offers.extend(weekly_offers)
        except Exception as exc:  # pragma: no cover
            logger.error("target: weekly ad failed: %s", exc)

        # Circle offers
        try:
            circle_offers = await self._fetch_circle_offers()
            all_offers.extend(circle_offers)
        except Exception as exc:  # pragma: no cover
            logger.error("target: circle offers failed: %s", exc)

        if not all_offers:
            logger.warning("target: no offers retrieved")
            return [], metadata

        return all_offers, metadata

    # ----------------------------------------------------------- weekly ad

    async def _fetch_weekly_ad(self) -> list[OfferData]:
        resp = await self.fetch_with_retry(TARGET_WEEKLY_AD)
        if resp is None:
            return []
        self.save_raw_payload({"html": resp.text[:50000]}, suffix="_weekly_html")
        return self._parse_weekly_html(resp.text)

    def _parse_weekly_html(self, html: str) -> list[OfferData]:
        offers: list[OfferData] = []

        # Target embeds data in __NEXT_DATA__ or JSON-LD scripts
        json_match = re.search(
            r'<script[^>]*(?:id="__NEXT_DATA__"|type="application/json")[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                offers.extend(self._extract_from_next_data(data))
            except (ValueError, TypeError) as exc:
                logger.debug("target: __NEXT_DATA__ parse failed: %s", exc)

        # Fallback: regex for product/price patterns
        if not offers:
            for m in re.finditer(
                r'>([^<]{3,80}?)\s+\$?(\d+\.\d{2})[^<]*<', html
            ):
                name = m.group(1).strip()
                price = self.dollars_to_cents(m.group(2))
                if price and name:
                    offers.append(
                        OfferData(
                            raw_text=f"{name} ${m.group(2)}",
                            product_name=name,
                            price=price,
                            deal_type="sale",
                        )
                    )
        return offers

    def _extract_from_next_data(self, data: dict[str, Any]) -> list[OfferData]:
        """Navigate Target's Next.js data structure for weekly ad items."""
        offers: list[OfferData] = []
        # Walk the data looking for product-like objects
        _walk_for_products(data, offers, deal_type="sale")
        return offers

    # ----------------------------------------------------------- circle

    async def _fetch_circle_offers(self) -> list[OfferData]:
        params = {"store_id": self.zip_or_store_id}
        resp = await self.fetch_with_retry(TARGET_CIRCLE_API, params=params)
        if resp is None:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        self.save_raw_payload(data, suffix="_circle")
        return self._parse_circle(data)

    def _parse_circle(self, data: dict[str, Any]) -> list[OfferData]:
        offers: list[OfferData] = []
        items = data.get("offers") or data.get("items") or []
        if isinstance(items, dict):
            items = list(items.values())
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("title") or item.get("name") or "").strip()
            if not name:
                continue
            # Circle offers may be % off or $ off
            price = self.dollars_to_cents(item.get("price") or item.get("value"))
            deal_type = "circle"
            # If it's a percentage, note in raw_text
            pct = item.get("percentage") or item.get("discountPercent")
            raw = name
            if pct:
                raw = f"{name} — {pct}% off (Circle)"
            elif price:
                raw = f"{name} — ${price / 100:.2f} off (Circle)"
            offers.append(
                OfferData(
                    raw_text=raw,
                    product_name=name,
                    brand=(item.get("brand") or "").strip(),
                    size_text=(item.get("size") or "").strip(),
                    price=price,
                    deal_type=deal_type,
                    requires_membership_or_coupon=True,  # must clip
                )
            )
        return offers


# ------------------------------------------------------------------ helpers


def _walk_for_products(
    obj: Any, offers: list[OfferData], deal_type: str = "sale"
) -> None:
    """Recursively walk a JSON tree looking for product-like dicts.

    A product-like dict has at least a ``name``/``title`` and a
    ``price``/``salePrice`` field.
    """
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("title") or obj.get("productName")
        price_raw = obj.get("price") or obj.get("salePrice") or obj.get("currentPrice")
        if name and price_raw:
            price = StoreAdapter.dollars_to_cents(price_raw)
            if price:
                offers.append(
                    OfferData(
                        raw_text=name,
                        product_name=str(name).strip(),
                        brand=(obj.get("brand") or "").strip(),
                        size_text=(obj.get("size") or obj.get("description") or "").strip(),
                        price=price,
                        deal_type=deal_type,
                    )
                )
        for v in obj.values():
            _walk_for_products(v, offers, deal_type)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_products(item, offers, deal_type)
