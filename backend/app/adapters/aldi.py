"""Aldi US weekly-ad adapter.

Data acquisition path
---------------------
1. **Primary — Flipp API** (backflipp.wishabi.com).  Aldi's digital circular is
   powered by Flipp, which syndicates the weekly ad as clean JSON with no
   bot-wall and no API key.  Flipp is keyed by **postal code**.  The
   :class:`FlippMixin` handles the two-step fetch (find flyers → fetch
   flyer detail).  We filter flyers by ``merchant_name`` matching "aldi".

2. **Fallback — browserless HTML scrape** of https://www.aldi.us/stores/en/weekly-ad/
   via the :class:`BrowserClient` (JS-rendered page), falling back to a raw
   httpx GET if the browserless service is unavailable.  Best-effort parse
   of any embedded JSON or visible offer text.

Ads run Wednesday→Tuesday.

ToS note (spec §5.2): Flipp's API is publicly accessible but undocumented.
We rate-limit to 2 s between requests and identify with a descriptive
User-Agent.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from .base import AdMetadata, OfferData, StoreAdapter
from .flipp_mixin import FlippMixin

logger = logging.getLogger(__name__)

ALDI_WEEKLY_AD_URL = "https://www.aldi.us/stores/en/weekly-ad/"


class AldiAdapter(FlippMixin, StoreAdapter):
    STORE_KEY = "aldi"
    STORE_NAME = "ALDI"
    DEFAULT_RATE_LIMIT = 2.0
    # Flipp merchant_name matcher for Aldi US
    FLIPP_MERCHANT_PATTERN = r"aldi"

    # ------------------------------------------------------------------ init

    def __init__(self, store_id: str = "aldi", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    # --------------------------------------------------------------- public

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Aldi's current weekly ad via Flipp, falling back to HTML."""
        metadata = AdMetadata(store_location=self.zip_or_store_id)

        # Try Flipp first (thread ZIP code into the API call)
        try:
            offers, meta = await self.fetch_flipp(self.zip_or_store_id)
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("aldi: Flipp path failed: %s", exc)

        # Fallback: HTML scrape via browserless
        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover — defensive
            logger.error("aldi: HTML fallback failed: %s", exc)

        logger.warning("aldi: no offers retrieved; returning empty list")
        return [], metadata

    # --------------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        if not await self.check_robots_txt(ALDI_WEEKLY_AD_URL):
            logger.info("aldi: robots.txt disallows %s", ALDI_WEEKLY_AD_URL)
            return [], AdMetadata(store_location=self.zip_or_store_id)

        # Try browserless first for JS-rendered page
        from .base import BrowserClient

        html: str | None = None
        try:
            bc = BrowserClient()
            html = await bc.render_page(ALDI_WEEKLY_AD_URL)
        except Exception as exc:
            logger.debug("aldi: browserless unavailable: %s", exc)

        if not html:
            resp = await self.fetch_with_retry(ALDI_WEEKLY_AD_URL)
            if resp is None:
                return [], AdMetadata(store_location=self.zip_or_store_id)
            html = resp.text

        self.save_raw_payload({"html": html[:50000]}, suffix="_html")

        offers: list[OfferData] = []
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
