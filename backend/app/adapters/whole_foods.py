"""Whole Foods Market adapter.

Data acquisition path
---------------------
Whole Foods (owned by Amazon) publishes weekly sale flyers and Prime member
deals at https://www.wholefoodsmarket.com/sales-flyer.  The page is
JS-rendered and accepts a ``store-id`` query parameter to localise the
deals.  The page structure shows:

  * Product name (brand + product description)
  * Prime member price ("$X.XX with Prime")
  * Regular sale price ("$X.XX" without Prime)
  * Original/regular price (strikethrough)
  * Validity dates ("Valid MM/DD - MM/DD", "Exp. MM/DD")
  * Deal type: "% off", "$X.XX ea", "$X.XX/lb", "N for $X"

1. **Primary — browserless HTML scrape** of
   ``https://www.wholefoodsmarket.com/sales-flyer?store-id={store_id}``
   using the :class:`BrowserClient` for JS rendering, falling back to a
   raw httpx GET.

2. **No separate API.**  Whole Foods doesn't expose a public JSON API for
   sales; the sales-flyer page is the source.  The page is heavily
   JS-rendered, so browserless is essential.

Both regular and Prime member prices are emitted as **separate** offers.
Prime member offers have ``requires_membership_or_coupon=True`` and
``deal_type="prime"``.

ToS note (spec §5.2): Whole Foods' ToS restricts automated access.  The
sanctioned path is the Amazon Product Advertising API (if access is granted)
or a licensed feed.  This adapter is written for completeness.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter, BrowserClient

logger = logging.getLogger(__name__)

WFM_SALES_URL = "https://www.wholefoodsmarket.com/sales-flyer"

# Regex to find validity date ranges like "Valid 07/29 - 08/04" or "Valid 07/29-08/04"
_DATE_RANGE_RE = re.compile(
    r"Valid\s+(\d{2}/\d{2})\s*[-–]\s*(\d{2}/\d{2})", re.IGNORECASE
)
# "Exp. 08/04"
_EXP_DATE_RE = re.compile(r"Exp\.?\s*(\d{2}/\d{2})", re.IGNORECASE)
# Price patterns: "$4.99 ea", "$3.29/lb", "2 for $6", "20% off", "$4.99"
_PRICE_EA_RE = re.compile(r"\$(\d+\.\d{2})\s*(?:ea|each)?", re.IGNORECASE)
_PRICE_LB_RE = re.compile(r"\$(\d+\.\d{2})\s*/?\s*lb", re.IGNORECASE)
_N_FOR_RE = re.compile(r"(\d+)\s*for\s*\$(\d+\.\d{2})", re.IGNORECASE)
_PCT_OFF_RE = re.compile(r"(\d+)%\s*off", re.IGNORECASE)


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

        try:
            offers, meta = await self._fetch_via_html()
            if offers:
                return offers, meta
        except Exception as exc:  # pragma: no cover
            logger.error("whole_foods: HTML fetch failed: %s", exc)

        logger.warning("whole_foods: no offers retrieved")
        return [], metadata

    # ----------------------------------------------------------- HTML scrape

    async def _fetch_via_html(self) -> tuple[list[OfferData], AdMetadata]:
        # Thread store-id into the URL
        url = f"{WFM_SALES_URL}?store-id={self.zip_or_store_id}"
        if not await self.check_robots_txt(url):
            logger.info("whole_foods: robots.txt disallows %s", url)
            return [], AdMetadata(store_location=self.zip_or_store_id)

        html: str | None = None
        # Try browserless first (WFM page is JS-rendered)
        try:
            bc = BrowserClient()
            html = await bc.render_page(url, wait_for="networkidle", timeout=45000)
        except Exception as exc:
            logger.debug("whole_foods: browserless unavailable: %s", exc)

        if not html:
            resp = await self.fetch_with_retry(url)
            if resp is None:
                return [], AdMetadata(store_location=self.zip_or_store_id)
            html = resp.text

        self.save_raw_payload({"html": html[:50000]}, suffix="_html")
        return self._parse_html(html)

    def _parse_html(self, html: str) -> tuple[list[OfferData], AdMetadata]:
        offers: list[OfferData] = []

        # WFM pages embed JSON in __NEXT_DATA__ script tags
        json_match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if json_match:
            try:
                import json

                data = json.loads(json_match.group(1))
                offers.extend(self._extract_from_next_data(data))
            except (ValueError, TypeError) as exc:
                logger.debug("whole_foods: __NEXT_DATA__ parse failed: %s", exc)

        # Fallback: structured text parse from the rendered page.
        # The WFM sales flyer has a repeating card pattern with:
        #   [Valid ...] [Exp. ...] [Sale Bug] [image] [BrandProductName] [price with Prime] [price]
        if not offers:
            offers = self._parse_card_text(html)

        # Determine period from "Valid MM/DD - MM/DD" or "Exp. MM/DD"
        period_start, period_end = _extract_dates(html)

        meta = AdMetadata(
            period_start=period_start or date.today(),
            period_end=period_end or (date.today() + timedelta(days=6)),
            store_location=self.zip_or_store_id,
        )
        return offers, meta

    def _extract_from_next_data(self, data: dict[str, Any]) -> list[OfferData]:
        """Navigate WFM's Next.js data structure for sale items."""
        offers: list[OfferData] = []
        props = data.get("props", {}).get("pageProps", {})
        sales = (
            props.get("sales")
            or props.get("products")
            or props.get("deals")
            or []
        )
        if isinstance(sales, dict):
            sales = list(sales.values())
        for item in sales:
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
        return offers

    def _parse_card_text(self, html: str) -> list[OfferData]:
        """Parse the WFM sales flyer card text from rendered HTML.

        The WFM flyer renders deal cards with this text structure:
          BrandProductName*
          $X.XX eawith Prime    (or "$X.XX/lbwith Prime", "N for $Xwith Prime")
          $X.XX ea              (regular sale price)
          $X.XX                 (original price)

        We split on "with Prime" to separate Prime and regular prices.
        """
        offers: list[OfferData] = []

        # Remove tags but preserve text to get a readable card stream
        text = re.sub(r"<[^>]+>", "\n", html)
        # Collapse whitespace
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        i = 0
        while i < len(lines):
            line = lines[i]

            # Look for "with Prime" lines indicating a deal card
            if "with Prime" in line:
                # The product name is typically a few lines above
                # Find the price in this line
                prime_price = _extract_price_from_line(line)
                if prime_price:
                    # Look backwards for a product name
                    name = ""
                    for j in range(i - 1, max(i - 5, -1), -1):
                        candidate = lines[j]
                        # Skip "Valid", "Exp.", "Sale Bug", image alt text
                        if (
                            candidate
                            and not candidate.startswith("Valid")
                            and not candidate.startswith("Exp")
                            and not candidate.startswith("Sale Bug")
                            and "with Prime" not in candidate
                            and "$" not in candidate
                            and len(candidate) > 3
                        ):
                            name = candidate.rstrip("*").strip()
                            break

                    if name:
                        # Determine deal type
                        deal_type = _classify_wfm_deal(line)
                        # Extract size from name if present
                        size_text = _extract_size(name)

                        offers.append(
                            OfferData(
                                raw_text=f"Prime: {name} {line}",
                                product_name=name,
                                size_text=size_text,
                                price=prime_price,
                                deal_type="prime",
                                requires_membership_or_coupon=True,
                            )
                        )

                        # Look for a regular sale price line (next line without "with Prime")
                        for j in range(i + 1, min(i + 3, len(lines))):
                            next_line = lines[j]
                            if "with Prime" not in next_line and "$" in next_line:
                                reg_price = _extract_price_from_line(next_line)
                                if reg_price and reg_price != prime_price:
                                    offers.append(
                                        OfferData(
                                            raw_text=f"{name} {next_line}",
                                            product_name=name,
                                            size_text=size_text,
                                            price=reg_price,
                                            deal_type="sale",
                                        )
                                    )
                                break
            i += 1

        return offers


# ------------------------------------------------------------------ helpers


def _extract_price_from_line(line: str) -> int:
    """Extract the first price (in cents) from a text line."""
    # Try "N for $X" first
    m = _N_FOR_RE.search(line)
    if m:
        total = float(m.group(2))
        n = int(m.group(1))
        return int(round((total / n) * 100))
    # Try "$X.XX/lb" or "$X.XX ea" or "$X.XX"
    m = _PRICE_EA_RE.search(line)
    if m:
        return StoreAdapter.dollars_to_cents(m.group(1))
    m = _PRICE_LB_RE.search(line)
    if m:
        return StoreAdapter.dollars_to_cents(m.group(1))
    return 0


def _classify_wfm_deal(line: str) -> str:
    """Classify a WFM deal line into a deal_type."""
    low = line.lower()
    if _PCT_OFF_RE.search(low):
        return "sale"  # percentage off
    if _N_FOR_RE.search(low):
        return "2_for"
    if "/lb" in low:
        return "sale"
    return "sale"


def _extract_size(name: str) -> str:
    """Extract a size description from a product name like 'Blueberries, 1 pt'."""
    m = re.search(r",\s*(\d+(?:\.\d+)?\s*(?:oz|lb|pt|qt|gal|ml|l|ct|pk|ea|fl\s*oz))", name, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _extract_dates(html: str) -> tuple[Optional[date], Optional[date]]:
    """Extract validity date range from the rendered HTML text."""
    text = re.sub(r"<[^>]+>", " ", html)
    # Try "Valid MM/DD - MM/DD"
    m = _DATE_RANGE_RE.search(text)
    if m:
        start = _mmdd_to_date(m.group(1))
        end = _mmdd_to_date(m.group(2))
        if start and end:
            return start, end
    # Try "Exp. MM/DD"
    m = _EXP_DATE_RE.search(text)
    if m:
        end = _mmdd_to_date(m.group(1))
        if end:
            return None, end
    return None, None


def _mmdd_to_date(mmdd: str) -> Optional[date]:
    """Convert 'MM/DD' to a date in the current year."""
    try:
        parts = mmdd.split("/")
        if len(parts) != 2:
            return None
        month, day = int(parts[0]), int(parts[1])
        year = date.today().year
        # Handle year rollover: if the month is earlier than current month,
        # it's likely next year
        if month < date.today().month:
            year += 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None
