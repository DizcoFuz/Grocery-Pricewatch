"""Mariano's adapter (Kroger banner).

Data acquisition path
---------------------
Mariano's is a Kroger banner.  Weekly ads run Wednesday→Tuesday.

1. **Primary — Flipp API** (backflipp.wishabi.com).  Kroger banners often
   appear on Flipp, keyed by **postal code**.  The :class:`FlippMixin`
   handles the two-step fetch.  We filter flyers by ``merchant_name``
   matching "mariano".

2. **Fallback — browserless HTML scrape** of the weekly ad page.

ToS note (spec §5.2): Kroger's ToS restricts automated scraping.  The
sanctioned path is the Kroger Developer API (requires OAuth).  This
adapter is written for completeness; production should use the official
Kroger API or a licensed feed.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from .base import AdMetadata, OfferData, StoreAdapter
from .flipp_mixin import FlippMixin

logger = logging.getLogger(__name__)

MARIANOS_AD_URL = "https://www.marianos.com/weeklyad"


class MarianosAdapter(FlippMixin, StoreAdapter):
    STORE_KEY = "marianos"
    STORE_NAME = "Mariano's"
    DEFAULT_RATE_LIMIT = 2.0
    FLIPP_MERCHANT_PATTERN = r"mariano"

    def __init__(self, store_id: str = "marianos", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        metadata = AdMetadata(
            period_start=_wednesday(),
            period_end=_wednesday() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        # Try Flipp first (thread ZIP code)
        try:
            offers, meta = await self.fetch_flipp(self.zip_or_store_id)
            if offers:
                logger.info("marianos: %d offers via flipp", len(offers))
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("marianos: flipp path failed: %s", exc)

        # Fallback: HTML scrape via browserless
        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                logger.info("marianos: %d offers via html", len(offers))
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("marianos: html path failed: %s", exc)

        logger.warning("marianos: no offers retrieved")
        return [], metadata

    # ----------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        if not await self.check_robots_txt(MARIANOS_AD_URL):
            logger.info("marianos: robots.txt disallows %s", MARIANOS_AD_URL)
            return [], AdMetadata(store_location=self.zip_or_store_id)

        from .base import BrowserClient

        html: str | None = None
        try:
            bc = BrowserClient()
            html = await bc.render_page(MARIANOS_AD_URL)
        except Exception as exc:
            logger.debug("marianos: browserless unavailable: %s", exc)

        if not html:
            resp = await self.fetch_with_retry(MARIANOS_AD_URL)
            if resp is None:
                return [], AdMetadata(store_location=self.zip_or_store_id)
            html = resp.text

        self.save_raw_payload({"html": html[:50000]}, suffix="_html")

        offers: list[OfferData] = []
        # Kroger embeds product data in JSON-LD or __NEXT_DATA__ scripts
        for m in re.finditer(
            r'"name"\s*:\s*"([^"]+)"[^}]*?"price"\s*:\s*"?(\d+\.?\d*)"?', html
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
        # Fallback regex for plain text offers
        if not offers:
            for m in re.finditer(r'>([^<]{3,80}?)\s+\$?(\d+\.\d{2})[^<]*<', html):
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
    today = date.today()
    return today - timedelta(days=(today.weekday() - 2) % 7)
