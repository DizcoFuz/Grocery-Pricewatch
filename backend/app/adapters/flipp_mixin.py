"""Shared Flipp API mixin for stores powered by Flipp circulars.

Flipp (backflipp.wishabi.com) syndicates weekly ads for many US/CA grocery
chains as clean JSON with no bot-wall and no API key.  The data is keyed by
**postal code**, so a chain's weekly ad is regional.

Data-acquisition flow (two-step, matching the verified public Flipp API):

1. ``GET /flipp/flyers?locale=en-us&postal_code={zip}``
   → list of flyers; filter by ``merchant_name`` matching the chain.
2. ``GET /flipp/flyers/{flyer_id}?locale=en-us&postal_code={zip}``
   → flyer detail containing ``flyer_items`` (or ``items`` / ``ecom_items``).

Item fields (current Flipp shape): ``name``, ``current_price`` (or ``price``
as a string), ``original_price``, ``sale_story``, ``pre_price_text``,
``post_price_text``, ``display_type`` (5 = banner/promo tile, skip),
``ttm_url`` (skip), ``valid_to``, ``cutout_image_url``, ``brand``.

References:
  - https://github.com/justinkuzmanich/scoop-alert  (verified Safeway/Flipp path)

ToS note (spec §5.2): Flipp's API is publicly accessible but undocumented.
We rate-limit to 2 s between requests and identify with a descriptive
User-Agent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from .base import AdMetadata, OfferData

logger = logging.getLogger(__name__)

FLIPP_BASE = "https://backflipp.wishabi.com/flipp"


class FlippMixin:
    """Shared Flipp API access for stores powered by Flipp circulars.

    Subclasses set ``FLIPP_MERCHANT_PATTERN`` (a regex/substring string used
    to match ``merchant_name`` in the flyers list) and call
    :meth:`fetch_flipp` from their ``fetch_current_ad``.
    """

    FLIPP_BASE = FLIPP_BASE
    #: Substring or regex to match the chain's ``merchant_name`` in the
    #  flyers list.  Subclasses must override this.
    FLIPP_MERCHANT_PATTERN: str = ""

    # display_type 5 = "download the app" / banner promo tile, not a product.
    _SKIP_DISPLAY_TYPES = {5}

    async def fetch_flipp(
        self, postal_code: str
    ) -> tuple[list[OfferData], AdMetadata]:
        """Fetch all current Flipp flyer items for ``postal_code``.

        Filters flyers by ``FLIPP_MERCHANT_PATTERN`` and returns combined
        items from all matching, currently-valid flyers.  Returns
        ``([], AdMetadata(...))`` on any failure.
        """
        if not postal_code or postal_code == "default":
            logger.warning(
                "%s: no postal code provided for Flipp lookup",
                getattr(self, "STORE_KEY", "?"),
            )
            return [], AdMetadata(store_location=postal_code)

        import re

        merchant_re = re.compile(
            self.FLIPP_MERCHANT_PATTERN or self.STORE_NAME,
            re.IGNORECASE,
        )

        # Step 1: find current flyers for this postal code
        flyers_url = f"{self.FLIPP_BASE}/flyers"
        params = {"locale": "en-us", "postal_code": postal_code}
        resp = await self.fetch_with_retry(flyers_url, params=params)  # type: ignore[attr-defined]
        if resp is None:
            return [], AdMetadata(store_location=postal_code)

        try:
            flyers_data = resp.json()
        except ValueError:
            logger.warning("flipp: flyers response not JSON")
            return [], AdMetadata(store_location=postal_code)

        flyers = self._extract_flyers(flyers_data)
        # Filter to matching merchant + currently valid
        now = datetime.now(timezone.utc).timestamp() * 1000
        matching = []
        for f in flyers:
            mname = f.get("merchant_name") or f.get("merchant") or ""
            if not merchant_re.search(str(mname)):
                continue
            vf = f.get("valid_from")
            vt = f.get("valid_to")
            from_ts = self._parse_ts(vf) if vf else -float("inf")
            to_ts = self._parse_ts(vt) if vt else float("inf")
            if from_ts <= now <= to_ts:
                matching.append(f)

        if not matching:
            logger.info(
                "%s: no current Flipp flyers matched '%s' for %s",
                getattr(self, "STORE_KEY", "?"),
                self.FLIPP_MERCHANT_PATTERN,
                postal_code,
            )
            self.save_raw_payload(flyers_data, suffix="_flyers")  # type: ignore[attr-defined]
            return [], AdMetadata(store_location=postal_code)

        self.save_raw_payload(
            {"flyers": flyers_data, "matched": matching}, suffix="_flyers"  # type: ignore[attr-defined]
        )

        # Step 2: fetch each flyer's items
        all_offers: list[OfferData] = []
        period_start: Optional[date] = None
        period_end: Optional[date] = None

        for f in matching:
            fid = f.get("id") or f.get("flyer_id")
            if not fid:
                continue
            detail_url = f"{self.FLIPP_BASE}/flyers/{fid}"
            dresp = await self.fetch_with_retry(detail_url, params=params)  # type: ignore[attr-defined]
            if dresp is None:
                continue
            try:
                detail = dresp.json()
            except ValueError:
                continue
            items = self._find_items_array(detail)
            for item in items:
                offer = self._map_flipp_item(item)
                if offer:
                    all_offers.append(offer)
            # Track period from flyer-level valid_from/valid_to
            fs = self._parse_date(f.get("valid_from"))
            fe = self._parse_date(f.get("valid_to"))
            if fs and (period_start is None or fs < period_start):
                period_start = fs
            if fe and (period_end is None or fe > period_end):
                period_end = fe

        meta = AdMetadata(
            period_start=period_start or date.today(),
            period_end=period_end or (date.today() + timedelta(days=6)),
            store_location=postal_code,
        )
        logger.info(
            "%s: %d offers from %d Flipp flyers",
            getattr(self, "STORE_KEY", "?"),
            len(all_offers),
            len(matching),
        )
        return all_offers, meta

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _extract_flyers(data: Any) -> list[dict]:
        if isinstance(data, list):
            return [f for f in data if isinstance(f, dict)]
        if isinstance(data, dict):
            fl = data.get("flyers")
            if isinstance(fl, list):
                return [f for f in fl if isinstance(f, dict)]
        return []

    @staticmethod
    def _find_items_array(json_obj: Any) -> list[dict]:
        """Find the array of items in a Flipp flyer-detail response.

        Flipp has used ``flyer_items``, ``items``, and ``ecom_items``.
        Falls back to any array whose elements are dicts with a ``name``.
        """
        if isinstance(json_obj, dict):
            for key in ("flyer_items", "items", "ecom_items"):
                arr = json_obj.get(key)
                if isinstance(arr, list) and arr:
                    return [i for i in arr if isinstance(i, dict)]
            for v in json_obj.values():
                if (
                    isinstance(v, list)
                    and v
                    and isinstance(v[0], dict)
                    and "name" in v[0]
                ):
                    return [i for i in v if isinstance(i, dict)]
        elif isinstance(json_obj, list):
            return [i for i in json_obj if isinstance(i, dict)]
        return []

    @staticmethod
    def _parse_ts(val: Any) -> float:
        """Parse a Flipp timestamp (ISO string or epoch seconds/ms)."""
        if val is None:
            return float("inf")
        if isinstance(val, (int, float)):
            # epoch ms if large, epoch seconds if small
            return float(val) if val > 1e12 else float(val) * 1000
        try:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            return dt.timestamp() * 1000
        except (ValueError, TypeError):
            return float("inf")

    @staticmethod
    def _parse_date(val: Any) -> Optional[date]:
        if not val:
            return None
        if isinstance(val, date):
            return val
        if isinstance(val, (int, float)):
            ts = val / 1000 if val > 1e12 else val
            try:
                return datetime.utcfromtimestamp(ts).date()
            except (OSError, ValueError):
                return None
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            return None

    def _map_flipp_item(self, item: dict) -> Optional[OfferData]:
        """Map a single Flipp flyer item to an OfferData, or None to skip."""
        name = (item.get("name") or "").strip()
        if not name:
            return None
        # Skip banners/promo tiles
        if item.get("display_type") in self._SKIP_DISPLAY_TYPES:
            return None
        if item.get("ttm_url"):  # ttm = "tap to merchant" promo
            return None

        # Price: current Flipp uses `current_price` (numeric) or `price`
        # (string; "" when priceless e.g. BOGO)
        price_raw = item.get("current_price")
        if price_raw is None:
            price_raw = item.get("price")
        price = self.dollars_to_cents(price_raw)  # type: ignore[attr-defined]

        original_price = self.dollars_to_cents(item.get("original_price"))  # type: ignore[attr-defined]

        deal_text = (
            (item.get("sale_story") or "").strip()
            or " ".join(
                t
                for t in (item.get("pre_price_text"), item.get("post_price_text"))
                if t
            ).strip()
            or "Weekly ad price"
        )

        size_text = (item.get("size") or item.get("description") or "").strip()
        brand = (item.get("brand") or "").strip()

        deal_type = self._classify_flipp_deal(item, deal_text)

        return OfferData(
            raw_text=deal_text,
            product_name=name,
            brand=brand,
            size_text=size_text,
            price=price,
            deal_type=deal_type,
            requires_membership_or_coupon=False,
        )

    @staticmethod
    def _classify_flipp_deal(item: dict, deal_text: str) -> str:
        text = deal_text.lower()
        if "buy one get one" in text or "bogo" in text:
            return "bogo"
        if "2 for" in text or "2/" in text:
            return "2_for"
        if "rollback" in text:
            return "rollback"
        # If there's an original_price and the price is lower, it's a sale
        if item.get("original_price") and item.get("current_price"):
            try:
                if float(item["current_price"]) < float(item["original_price"]):
                    return "sale"
            except (ValueError, TypeError):
                pass
        return "sale"
