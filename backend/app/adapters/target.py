"""Target adapter — weekly ad + Circle offers.

Data acquisition path
---------------------
Target exposes:
  * A weekly ad at https://www.target.com/weeklyad (JS-rendered HTML)
  * Target Circle offers via the RedCircle API
  * Store content via redsky.target.com aggregation APIs

1. **Primary — Flipp API** (backflipp.wishabi.com).  Target's weekly ad is
   also syndicated on Flipp, keyed by **postal code**.  This gives us
   structured JSON with no bot-wall.  We filter flyers by
   ``merchant_name`` matching "target".

2. **Weekly ad HTML fallback — browserless** scrape of
   https://www.target.com/weeklyad, parsing embedded __NEXT_DATA__ JSON.

3. **Circle offers — Target RedCircle API.**  Target Circle offers are
   served via ``https://redsky.target.com/red_aggregations/v1/web/plp_search``
   or the weekly ad API.  We attempt the redsky aggregation endpoint with the
   store_id (threaded from zip_or_store_id).

Circle offers require the user to "clip" them in the Target app, so we set
``requires_membership_or_coupon=True`` on those offers with ``deal_type="circle"``.

ToS note (spec §5.2): Target's ToS restricts automated access.  The
sanctioned path is the Target Partners API (if granted).  This adapter
is written for completeness.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

from .base import AdMetadata, OfferData, StoreAdapter, BrowserClient
from .flipp_mixin import FlippMixin

logger = logging.getLogger(__name__)

TARGET_WEEKLY_AD = "https://www.target.com/weeklyad"
# Target's redsky aggregation API (verified public key from Target's frontend)
TARGET_REDSKY_KEY = "8df66ea1e1fc070a6ea99e942431c9cd67a80f02"
TARGET_CIRCLE_API = (
    "https://redsky.target.com/red_aggregations/v1/web/plp_search"
)


class TargetAdapter(FlippMixin, StoreAdapter):
    STORE_KEY = "target"
    STORE_NAME = "Target"
    DEFAULT_RATE_LIMIT = 2.0
    FLIPP_MERCHANT_PATTERN = r"target"

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

        # Primary: Flipp (thread ZIP code)
        try:
            flipp_offers, meta = await self.fetch_flipp(self.zip_or_store_id)
            if flipp_offers:
                all_offers.extend(flipp_offers)
        except Exception as exc:  # pragma: no cover
            logger.error("target: Flipp path failed: %s", exc)

        # Fallback: HTML weekly ad (browserless)
        if not all_offers:
            try:
                weekly_offers = await self._fetch_weekly_ad()
                all_offers.extend(weekly_offers)
            except Exception as exc:  # pragma: no cover
                logger.error("target: weekly ad failed: %s", exc)

        # Circle offers (always attempt — separate data source)
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
        if not await self.check_robots_txt(TARGET_WEEKLY_AD):
            logger.info("target: robots.txt disallows %s", TARGET_WEEKLY_AD)
            return []

        html: str | None = None
        try:
            bc = BrowserClient()
            html = await bc.render_page(TARGET_WEEKLY_AD)
        except Exception as exc:
            logger.debug("target: browserless unavailable: %s", exc)

        if not html:
            resp = await self.fetch_with_retry(TARGET_WEEKLY_AD)
            if resp is None:
                return []
            html = resp.text

        self.save_raw_payload({"html": html[:50000]}, suffix="_weekly_html")
        return self._parse_weekly_html(html)

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
        _walk_for_products(data, offers, deal_type="sale")
        return offers

    # ----------------------------------------------------------- circle

    async def _fetch_circle_offers(self) -> list[OfferData]:
        # Thread store_id into the API call
        params = {
            "key": TARGET_REDSKY_KEY,
            "channel": "WEB",
            "store_id": self.zip_or_store_id,
        }
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
