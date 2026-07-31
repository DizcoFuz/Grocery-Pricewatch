"""Jewel-Osco adapter (Albertsons banner).

Data acquisition path
---------------------
Jewel-Osco is an Albertsons Companies banner.  Weekly ads typically run
Wednesday→Tuesday.

1. **Primary — Flipp API.** Albertsons banners are frequently on Flipp.
   We try ``https://flipp.com/items/weekly?locale=en-US&store_code=jewel-osco``.

2. **Secondary — Albertsons digital circular API.** Jewel-Osco serves a
   weekly-ad endpoint at ``https://www.jewelosco.com/weeklyad/v1/weeklyad``
   (exact path may vary; the Albertsons family uses an internal circular
   service).  We attempt this and parse JSON.

3. **Fallback — HTML scrape** of ``https://www.jewelosco.com/weeklyad.html``.

ToS note (spec §5.2): Albertsons' ToS restricts automated access.  Use the
official Albertsons Developer / partner feed where available.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

FLIPP_ENDPOINT = "https://flipp.com/items/weekly"
FLIPP_STORE_CODE = "jewel-osco"
JEWEL_API = "https://www.jewelosco.com/weeklyad/v1/weeklyad"
JEWEL_HTML = "https://www.jewelosco.com/weeklyad.html"


class JewelOscoAdapter(StoreAdapter):
    STORE_KEY = "jewel_osco"
    STORE_NAME = "Jewel-Osco"
    DEFAULT_RATE_LIMIT = 2.0

    def __init__(self, store_id: str = "jewel_osco", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        metadata = AdMetadata(
            period_start=_wednesday(),
            period_end=_wednesday() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        for label, fn in (
            ("flipp", self._fetch_via_flipp),
            ("api", self._fetch_via_api),
            ("html", self._fetch_via_html),
        ):
            try:
                offers, meta = await fn()
                if offers:
                    logger.info("jewel_osco: %d offers via %s", len(offers), label)
                    return offers, meta
            except Exception as exc:  # pragma: no cover
                logger.error("jewel_osco: %s path failed: %s", label, exc)

        logger.warning("jewel_osco: no offers retrieved")
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
        return _parse_flipp(data, self.zip_or_store_id)

    # ----------------------------------------------------------- API

    async def _fetch_via_api(self) -> tuple[list[OfferData], AdMetadata]:
        params = {"store_id": self.zip_or_store_id}
        resp = await self.fetch_with_retry(JEWEL_API, params=params)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        try:
            data = resp.json()
        except ValueError:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload(data, suffix="_api")
        return self._parse_jewel_api(data)

    def _parse_jewel_api(self, data: dict[str, Any]) -> tuple[list[OfferData], AdMetadata]:
        offers: list[OfferData] = []
        items = data.get("items") or data.get("offers") or data.get("products") or []
        if isinstance(items, dict):
            items = list(items.values())
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("title") or item.get("name") or item.get("productName") or "").strip()
            if not name:
                continue
            price = self.dollars_to_cents(
                item.get("price") or item.get("salePrice") or item.get("currentPrice")
            )
            size_text = (item.get("size") or item.get("description") or "").strip()
            deal_type = _classify_deal(item)
            brand = (item.get("brand") or "").strip()
            offers.append(
                OfferData(
                    raw_text=item.get("description") or name,
                    product_name=name,
                    brand=brand,
                    size_text=size_text,
                    price=price,
                    deal_type=deal_type,
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
        resp = await self.fetch_with_retry(JEWEL_HTML)
        if resp is None:
            return [], AdMetadata(store_location=self.zip_or_store_id)
        self.save_raw_payload({"html": resp.text[:50000]}, suffix="_html")
        offers: list[OfferData] = []
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
        for v in (item.get("description"), item.get("title"), item.get("promo"))
        if v
    )
    if "bogo" in text or "buy one get one" in text:
        return "bogo"
    if "2 for" in text or "2/" in text:
        return "2_for"
    if "member" in text or "just for u" in text:
        return "member"
    return "sale"


def _parse_flipp(
    data: dict[str, Any], location: str
) -> tuple[list[OfferData], AdMetadata]:
    """Shared Flipp parser (same shape as Aldi's)."""
    items = data.get("items") or data.get("flyer_items") or []
    offers: list[OfferData] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("product_name") or "").strip()
        if not name:
            continue
        price = StoreAdapter.dollars_to_cents(item.get("price"))
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
        store_location=location,
    )
    return offers, meta
