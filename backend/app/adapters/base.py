"""Abstract StoreAdapter base class for the Grocery Pricewatch app.

Defines the common interface every store adapter must implement and shared
infrastructure: rate limiting, HTTP retry, raw-payload archival, and the
``OfferData`` / ``AdMetadata`` dataclasses.

Spec references (grocery-price-tracker-spec.md):
  §5.2 — Be polite: ≥2s between requests per host; respect robots.txt.
  §5.x — All prices in integer cents; raw payloads archived 90 days.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OfferData:
    """A single normalized offer extracted from a store's weekly ad.

    All monetary values are in **integer cents** (USD) to avoid float drift.
    """

    raw_text: str = ""
    product_name: str = ""
    brand: str = ""
    size_text: str = ""
    price: int = 0  # sale price in cents
    deal_type: str = "sale"  # sale, rollback, 2_for, bogo, circle, prime, ...
    effective_unit_price: int = 0  # cents per unit (e.g. per oz, per lb)
    unit_price_unknown: bool = True
    requires_membership_or_coupon: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdMetadata:
    """Metadata describing a fetched ad circular."""

    period_start: Optional[date] = None
    period_end: Optional[date] = None
    store_location: str = ""
    raw_payload_ref: str = ""


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

# Where raw payloads are archived.  Resolved relative to the project root at
# runtime so it works both in Docker (/data/...) and local dev.
_DATA_DIR = Path("/data/raw_payloads")
_LOCAL_FALLBACK = Path(__file__).resolve().parents[4] / "data" / "raw_payloads"


class StoreAdapter(ABC):
    """Abstract base class for all store adapters.

    Subclasses implement :meth:`fetch_current_ad` and set ``STORE_KEY``,
    ``STORE_NAME``, and ``DEFAULT_RATE_LIMIT`` class attributes.
    """

    STORE_KEY: str = "base"
    STORE_NAME: str = "Base Store"
    DEFAULT_RATE_LIMIT: float = 2.0  # seconds between requests

    def __init__(self, store_id: str, zip_or_store_id: str) -> None:
        """``store_id`` is the adapter key (e.g. ``"aldi"``);
        ``zip_or_store_id`` is a ZIP code or store-specific identifier used to
        localise the ad."""
        self.store_id = store_id
        self.zip_or_store_id = zip_or_store_id
        self._last_request_ts: float = 0.0
        self._rate_limit_seconds: float = self.DEFAULT_RATE_LIMIT

    # -- abstract ------------------------------------------------------------

    @abstractmethod
    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch the current weekly ad for this store.

        Returns a tuple of ``(offers, metadata)``.  On any failure the
        implementation should return ``( [], AdMetadata() )`` and log the
        error — it must **never** raise.
        """
        ...

    # -- shared helpers ------------------------------------------------------

    async def rate_limit(self, seconds: Optional[float] = None) -> None:
        """Sleep so that at least ``rate_limit_seconds`` elapse between
        requests to the same host (spec §5.2)."""
        wait = seconds if seconds is not None else self._rate_limit_seconds
        elapsed = asyncio.get_event_loop().time() - self._last_request_ts
        if elapsed < wait:
            await asyncio.sleep(wait - elapsed)
        self._last_request_ts = asyncio.get_event_loop().time()

    async def fetch_with_retry(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> Optional[httpx.Response]:
        """Perform an HTTP request with exponential backoff.

        Returns the :class:`httpx.Response` on success, or ``None`` after all
        retries are exhausted.  Rate-limits before each attempt.
        """
        hdrs = {
            "User-Agent": (
                "GroceryPricewatchBot/1.0 (+https://github.com/example/grocery-pricewatch)"
            ),
            "Accept": "application/json, text/html, */*",
        }
        if headers:
            hdrs.update(headers)

        backoff = 1.0
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            await self.rate_limit()
            try:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True
                ) as client:
                    resp = await client.request(
                        method, url, headers=hdrs, params=params, json=json_body
                    )
                    if resp.status_code < 500:
                        return resp
                    logger.warning(
                        "%s: HTTP %d on %s (attempt %d/%d)",
                        self.STORE_KEY,
                        resp.status_code,
                        url,
                        attempt,
                        retries,
                    )
            except (httpx.RequestError, httpx.HTTPError) as exc:
                last_exc = exc
                logger.warning(
                    "%s: request error on %s (attempt %d/%d): %s",
                    self.STORE_KEY,
                    url,
                    attempt,
                    retries,
                    exc,
                )
            await asyncio.sleep(backoff)
            backoff *= 2
        logger.error(
            "%s: all %d retries exhausted for %s: %s",
            self.STORE_KEY,
            retries,
            url,
            last_exc,
        )
        return None

    # -- raw payload archival ------------------------------------------------

    @staticmethod
    def _resolve_data_dir() -> Path:
        """Return the directory for raw payloads, creating it if needed."""
        d = _DATA_DIR
        if not d.exists() or not d.is_dir():
            d = _LOCAL_FALLBACK
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_raw_payload(self, data: Any, suffix: str = "") -> str:
        """Persist a raw payload (JSON-serialisable) and return its path.

        Filenames look like ``{store_key}_{YYYYMMDD}{suffix}.json``.  Existing
        files are never overwritten (a counter is appended).
        """
        d = self._resolve_data_dir()
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        name = f"{self.STORE_KEY}_{ts}{suffix}.json"
        path = d / name
        counter = 1
        while path.exists():
            path = d / f"{self.STORE_KEY}_{ts}{suffix}_{counter}.json"
            counter += 1
        try:
            text = json.dumps(data, indent=2, default=str, ensure_ascii=False)
            path.write_text(text, encoding="utf-8")
        except (TypeError, ValueError) as exc:
            # fall back to string representation
            path.write_text(str(data), encoding="utf-8")
            logger.warning("%s: raw payload saved as string: %s", self.STORE_KEY, exc)
        logger.info("%s: raw payload saved to %s", self.STORE_KEY, path)
        return str(path)

    # -- robots.txt ----------------------------------------------------------

    async def check_robots_txt(self, base_url: str) -> bool:
        """Best-effort robots.txt check.

        Returns ``True`` if the path is allowed (or if robots.txt is
        unreachable).  ``False`` if explicitly disallowed.
        """
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    base_url.rstrip("/").rsplit("/", 1)[0] + "/robots.txt"
                    if "/" in base_url[8:]
                    else base_url.rstrip("/") + "/robots.txt"
                )
                if resp.status_code != 200:
                    return True  # no robots.txt → assume allowed
                # very naive parse — full RFC 9309 parser is overkill here
                for line in resp.text.splitlines():
                    line = line.strip()
                    if line.lower().startswith("disallow: /"):
                        return False
            return True
        except httpx.RequestError:
            return True  # can't fetch → don't block

    # -- price / size parsing utilities --------------------------------------

    _PRICE_RE = __import__("re").compile(
        r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"
    )

    @staticmethod
    def dollars_to_cents(value: float | str | None) -> int:
        """Convert a dollar value to integer cents."""
        if value is None:
            return 0
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
            if not value:
                return 0
        try:
            return int(round(float(value) * 100))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def parse_price_text(text: str) -> int:
        """Extract the first dollar amount from ``text`` as cents."""
        import re

        m = re.search(r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)", text or "")
        if not m:
            return 0
        return StoreAdapter.dollars_to_cents(m.group(1))
