"""Mariano's adapter (Kroger banner).

Data acquisition path
---------------------
Mariano's is a Kroger banner.  Weekly ads typically run Wednesday→Tuesday.

1. **Primary — Flipp API.** Kroger banners often appear on Flipp::

       https://flipp.com/items/weekly?locale=en-US&store_code=marianos

2. **Secondary — Kroger public circular API** (if exposed without auth):
       https://www.marianos.com/weeklyad

3. **Fallback — HTML scrape** of the weekly ad page.

ToS note (spec §5.2): Kroger's ToS restricts automated scraping.  The
sanctioned path is the Kroger Developer API (requires OAuth).  This
adapter is written for completeness; production should use the official
Kroger API or a licensed feed.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

FLIPP_ENDPOINT = "https://flipp.com/items/weekly"
FLIPP_STORE_CODE = "marianos"
MARIANOS_AD_URL = "https://www.marianos.com/weeklyad"


class MarianosAdapter(StoreAdapter):
    STORE_KEY = "marianos"
    STORE_NAME = "Mariano's"
    DEFAULT_RATE_LIMIT = 2.0

    def __init__(self, store_id: str = "marianos", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        metadata = AdMetadata(
            period_start=_wednesday(),
            period_end=_wednesday() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        for label, fn in (
            ("flipp", self._fetch_via_flipp),
            ("html", self._fetch_via_html),
        ):
            try:
                offers, meta = await fn()
                if offers:
                    logger.info("marianos: %d offers via %s", len(offers), label)
                    return offers, meta
            except Exception as exc:  # pragma: no cover
                logger.error("marianos: %s path failed: %s", label, exc)

        logger.warning("marianos: no offers retrieved")
        return [], metadata

    # ----------------------------------------------------------- Flipp

    async def _fetch_via_flipp(self) -> tuple[list[OfferData], AdMetadata]:
        params = {"locale": "en-US", "store_code": FLIPP_STORE_CODE}
        resp = await self.fetch_with_retry(FLIPP_ENDPOINT, params=params)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        try:
            data = resp.json()
        except ValueError:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload(data, suffix="_flipp")
        return self._parse_flipp(data)

    def _parse_flipp(self, data: dict[str, Any]) -> tuple[list[OfferData], AdMetadata]:
        items = data.get("items") or data.get("flyer_items") or []
        offers: list[OfferData] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or item.get("product_name") or "").strip()
            if not name:
                continue
            price = self.dollars_to_cents(item.get("price"))
            offers.append(
                OfferData(
                    raw_text=item.get("sale_story") or name,
                    product_name=name,
                    brand=(item.get("brand") or "").strip(),
                    size_text=(item.get("size") or item.get("description") or "").strip(),
                    price=price,
                    deal_type=_classify_deal(item),
                )
            )
        meta = AdMetadata(
            period_start=_wednesday(),
            period_end=_wednesday() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )
        return offers, meta

    # ----------------------------------------------------------- HTML

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        resp = await self.fetch_with_retry(MARIANOS_AD_URL)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload({"html": resp.text[:50000]}, suffix="_html")

        offers: list[OfferData] = []
        # Kroger embeds product data in JSON-LD or __NEXT_DATA__ scripts
        for m in re.finditer(r'"name"\s*:\s*"([^"]+)"[^}]*?"price"\s*:\s*"?(\d+\.?\d*)"?', resp.text):
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
            for m in re.finditer(r'>([^<]{3,80}?)\s+\$?(\d+\.\d{2})[^<]*<', resp.text):
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


def _classify_deal(item: dict[str, Any]) -> str:
    text = " ".join(
        str(v).lower()
        for v in (item.get("description"), item.get("sale_story"), item.get("title"))
        if v
    )
    if "bogo" in text or "buy one get one" in text:
        return "bogo"
    if "2 for" in text or "2/" in text:
        return "2_for"
    if "member" in text:
        return "member"
    return "sale"
