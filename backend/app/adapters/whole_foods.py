"""Whole Foods Market adapter.

Data acquisition path
---------------------
Whole Foods (owned by Amazon) publishes weekly sale flyers and Prime member
deals at https://www.wholefoodsmarket.com/sales-flyer.

1. **Primary** — Scrape the sales-flyer page, which lists both regular sale
   prices and Prime member exclusive prices.
2. **Secondary** — Try the store-locator-driven sale API if available.

Both regular and Prime member prices are emitted as **separate** offers.
Prime member offers have ``requires_membership_or_coupon=True``.

ToS note (spec §5.2): Whole Foods' ToS restricts automated access.  The
sanctioned path is the Amazon Product Advertising API (if access is granted)
or a licensed feed.  This adapter is written for completeness.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

WFM_SALES_URL = "https://www.wholefoodsmarket.com/sales-flyer"
WFM_STORE_API = "https://www.wholefoodsmarket.com/api/store/{store_id}/sales"


class WholeFoodsAdapter(StoreAdapter):
    STORE_KEY = "whole_foods"
    STORE_NAME = "Whole Foods Market"
    DEFAULT_RATE_LIMIT = 2.0

    def __init__(
        self, store_id: str = "whole_foods", zip_or_store_id: str = ""
    ) -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Whole Foods weekly sales + Prime member deals."""
        metadata = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        # Try store API first
        try:
            offers, meta = await self._fetch_via_api()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("whole_foods: API path failed: %s", exc)

        # Fallback: HTML scrape
        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("whole_foods: HTML fallback failed: %s", exc)

        logger.warning("whole_foods: no offers retrieved")
        return [], metadata

    # ----------------------------------------------------------- API

    async def _fetch_via_api(self) -> tuple[list[OfferData], AdMetadata]:
        url = WFM_STORE_API.format(store_id=self.zip_or_store_id)
        resp = await self.fetch_with_retry(url)
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
        items = data.get("sales") or data.get("items") or data.get("products") or []
        if isinstance(items, dict):
            items = list(items.values())
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or item.get("title") or "").strip()
            if not name:
                continue
            brand = (item.get("brand") or "").strip()
            size_text = (item.get("size") or item.get("description") or "").strip()

            # Regular sale price
            reg_price = self.dollars_to_cents(
                item.get("salePrice") or item.get("price")
            )
            if reg_price:
                offers.append(
                    OfferData(
                        raw_text=item.get("description") or name,
                        product_name=name,
                        brand=brand,
                        size_text=size_text,
                        price=reg_price,
                        deal_type="sale",
                    )
                )

            # Prime member price (separate offer)
            prime_price = self.dollars_to_cents(item.get("primePrice"))
            if prime_price:
                offers.append(
                    OfferData(
                        raw_text=f"Prime: {item.get('description') or name}",
                        product_name=name,
                        brand=brand,
                        size_text=size_text,
                        price=prime_price,
                        deal_type="prime",
                        requires_membership_or_coupon=True,
                    )
                )
        meta = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )
        return offers, meta

    # ----------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        resp = await self.fetch_with_retry(WFM_SALES_URL)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload({"html": resp.text[:50000]}, suffix="_html")
        return self._parse_html(resp.text)

    def _parse_html(self, html: str) -> tuple[list[OfferData], AdMetadata]:
        offers: list[OfferData] = []

        # WFM pages often embed JSON in script tags
        # Try to find __NEXT_DATA__ or similar
        json_match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if json_match:
            try:
                import json

                data = json.loads(json_match.group(1))
                # Navigate common Next.js data paths
                props = data.get("props", {}).get("pageProps", {})
                sales = props.get("sales") or props.get("products") or []
                for item in sales:
                    name = (item.get("name") or item.get("title") or "").strip()
                    if not name:
                        continue
                    reg_price = self.dollars_to_cents(item.get("price"))
                    if reg_price:
                        offers.append(
                            OfferData(
                                raw_text=name,
                                product_name=name,
                                price=reg_price,
                                deal_type="sale",
                            )
                        )
                    prime_price = self.dollars_to_cents(item.get("primePrice"))
                    if prime_price:
                        offers.append(
                            OfferData(
                                raw_text=f"Prime: {name}",
                                product_name=name,
                                price=prime_price,
                                deal_type="prime",
                                requires_membership_or_coupon=True,
                            )
                        )
            except (ValueError, TypeError):
                pass

        # Fallback: regex parse for price patterns
        if not offers:
            for m in re.finditer(
                r'>([^<]{3,80}?)\s+\$?(\d+\.\d{2})[^<]*<', html
            ):
                name = m.group(1).strip()
                price = self.dollars_to_cents(m.group(2))
                if price and name:
                    is_prime = "prime" in name.lower()
                    offers.append(
                        OfferData(
                            raw_text=f"{name} ${m.group(2)}",
                            product_name=name,
                            price=price,
                            deal_type="prime" if is_prime else "sale",
                            requires_membership_or_coupon=is_prime,
                        )
                    )

        meta = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )
        return offers, meta
