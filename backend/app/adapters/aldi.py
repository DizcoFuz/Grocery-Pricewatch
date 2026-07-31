"""Aldi US weekly-ad adapter.

Data acquisition path
---------------------
1. **Primary — Flipp API.** Aldi's digital circular is powered by Flipp
   (https://flipp.com).  We query the public Flipp items endpoint::

       https://flipp.com/items/weekly?locale=en-US&store_code={code}

   The JSON response contains ``items`` with ``name``, ``price``,
   ``sale_story``, ``description``, and ``validity_dates``.  This is the
   most reliable path because Flipp provides structured JSON.

2. **Fallback — HTML scrape** of https://www.aldi.us/stores/en/weekly-ad/
   (fragile; Aldi's site is JS-heavy and may require a headless browser
   in production).  We attempt a best-effort parse of any embedded JSON
   or visible offer text.

Ads run Wednesday→Tuesday.

ToS note (spec §5.2): Flipp's API is publicly accessible but undocumented.
We rate-limit to 2 s between requests and identify with a descriptive
User-Agent.  If Flipp publishes a formal ToS prohibiting automated access,
this adapter would need to switch to an licensed feed or be disabled.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

FLIPP_ENDPOINT = "https://flipp.com/items/weekly"
FLIPP_STORE_CODE = "aldi-us"  # best-known Flipp store_code for Aldi US
ALDI_WEEKLY_AD_URL = "https://www.aldi.us/stores/en/weekly-ad/"


class AldiAdapter(StoreAdapter):
    STORE_KEY = "aldi"
    STORE_NAME = "ALDI"
    DEFAULT_RATE_LIMIT = 2.0

    # ------------------------------------------------------------------ init

    def __init__(self, store_id: str = "aldi", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")
        self._flipp_code = FLIPP_STORE_CODE

    # --------------------------------------------------------------- public

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Aldi's current weekly ad via Flipp, falling back to HTML."""
        metadata = AdMetadata(store_location=self.zip_or_store_id)

        # Try Flipp first
        try:
            offers, meta = await self._fetch_via_flipp()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("aldi: Flipp path failed: %s", exc)

        # Fallback: HTML scrape
        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("aldi: HTML fallback failed: %s", exc)

        logger.warning("aldi: no offers retrieved; returning empty list")
        return [], metadata

    # --------------------------------------------------------------- Flipp

    async def _fetch_via_flipp(self) -> tuple[list[OfferData], AdMetadata]:
        params = {"locale": "en-US", "store_code": self._flipp_code}
        resp = await self.fetch_with_retry(FLIPP_ENDPOINT, params=params)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)

        try:
            data: dict[str, Any] = resp.json()
        except ValueError:
            logger.warning("aldi: Flipp response not JSON")
            return [], AdMetadata(store_location=self.zip_or_store_id)

        self.save_raw_payload(data, suffix="_flipp")
        return self._parse_flipp(data)

    def _parse_flipp(self, data: dict[str, Any]) -> tuple[list[OfferData], AdMetadata]:
        items = data.get("items") or data.get("flyer_items") or []
        offers: list[OfferData] = []
        period_start: Optional[date] = None
        period_end: Optional[date] = None

        # Validity dates may be top-level or per-item
        top_validity = data.get("validity_dates") or {}
        if top_validity:
            period_start = _safe_date(top_validity.get("start"))
            period_end = _safe_date(top_validity.get("end"))

        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or item.get("product_name") or "").strip()
            if not name:
                continue
            price_raw = item.get("price") or item.get("current_price")
            price = self.dollars_to_cents(price_raw)
            size_text = (item.get("size") or item.get("description") or "").strip()
            deal_type = _classify_flipp_deal(item)
            brand = (item.get("brand") or "").strip()
            raw_text = item.get("sale_story") or item.get("description") or name

            # Per-item validity overrides top-level
            iv = item.get("validity_dates") or {}
            if iv and period_start is None:
                period_start = _safe_date(iv.get("start"))
                period_end = _safe_date(iv.get("end"))

            offers.append(
                OfferData(
                    raw_text=raw_text,
                    product_name=name,
                    brand=brand,
                    size_text=size_text,
                    price=price,
                    deal_type=deal_type,
                    requires_membership_or_coupon=False,
                )
            )

        meta = AdMetadata(
            period_start=period_start or _wednesday(),
            period_end=period_end or (_wednesday() + timedelta(days=6)),
            store_location=self.zip_or_store_id,
            raw_payload_ref="",
        )
        return offers, meta

    # --------------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        resp = await self.fetch_with_retry(ALDI_WEEKLY_AD_URL)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload({"html": resp.text[:50000]}, suffix="_html")

        offers: list[OfferData] = []
        # Naïve: look for dollar-prefixed offers near product keywords
        # Real implementation would use a JS-rendered DOM (Playwright).
        for m in re.finditer(
            r'>([^<]{3,80}?)\s+\$?(\d+\.\d{2})[^<]*<', resp.text
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
        meta = AdMetadata(
            period_start=_wednesday(),
            period_end=_wednesday() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )
        return offers, meta


# ------------------------------------------------------------------ helpers


def _wednesday() -> date:
    """Return the most recent Wednesday's date."""
    today = date.today()
    return today - timedelta(days=(today.weekday() - 2) % 7)


def _safe_date(val: Any) -> Optional[date]:
    if not val:
        return None
    if isinstance(val, date):
        return val
    try:
        return datetime.fromisoformat(str(val)).date()
    except (ValueError, TypeError):
        return None


def _classify_flipp_deal(item: dict[str, Any]) -> str:
    """Best-effort deal-type classification from Flipp fields."""
    story = (item.get("sale_story") or "").lower()
    desc = (item.get("description") or "").lower()
    text = f"{story} {desc}"
    if "buy one get one" in text or "bogo" in text:
        return "bogo"
    if "2 for" in text or "2/" in text:
        return "2_for"
    if "rollback" in text:
        return "rollback"
    return "sale"
