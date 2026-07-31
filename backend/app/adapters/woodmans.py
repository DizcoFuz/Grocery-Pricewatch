"""Woodman's Market adapter — OCR-based.

Data acquisition path
---------------------
Woodman's (woodmans-food.com) does **not** provide a structured digital
circular API.  Their weekly ad is distributed as PDF and/or images on
https://www.woodmans-food.com/weekly-ads (or similar).

1. **Primary** — Scrape the ads page for links to weekly-ad PDFs or images.
2. **Download** the PDF/image.
3. **OCR** via a tesseract HTTP service at ``http://tesseract:8080/ocr``
   (container in the docker-compose stack).
4. **Parse** the OCR text with regex patterns for price/size, then structure
   into :class:`OfferData`.
5. **Flag partial** if OCR confidence is low.

Rate limit is 3 s — be extra polite to a small regional chain.

ToS note (spec §5.2): Woodman's is a small regional chain.  We rate-limit
generously and only fetch the publicly-available ad page.  A formal data
partnership would be preferable if available.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any, Optional

from .base import AdMetadata, OfferData, StoreAdapter

logger = logging.getLogger(__name__)

WOODMANS_ADS_URL = "https://www.woodmans-food.com/weekly-ads"

# Regex patterns for price/size extraction from OCR text
_PRICE_RE = re.compile(r"\$?\s*(\d{1,2}\.\d{2})\b")
_SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(oz|lb|lbs|ounce|pound|g|gram|kg|ml|l|liter|"
    r"qt|pt|gal|fl\s*oz|each|ct|count|pk|pack|ea)\b",
    re.IGNORECASE,
)
# Offer line heuristic: a line with a price and some text
_OFFER_LINE_RE = re.compile(
    r"^(.+?)\s+\$?(\d{1,2}\.\d{2})\s*(?:/|per)?\s*(oz|lb|lbs|g|kg|ml|l|qt|pt|gal|each|ct|pk|ea)?\s*$",
    re.IGNORECASE,
)


class WoodmansAdapter(StoreAdapter):
    STORE_KEY = "woodmans"
    STORE_NAME = "Woodman's"
    DEFAULT_RATE_LIMIT = 3.0  # extra polite

    #: Minimum OCR confidence (0–100) below which we flag results as partial.
    OCR_CONFIDENCE_THRESHOLD = 60

    def __init__(self, store_id: str = "woodmans", zip_or_store_id: str = "") -> None:
        super().__init__(store_id, zip_or_store_id or "default")
        self.tesseract_url = os.environ.get(
            "TESSERACT_URL", "http://tesseract:8080/ocr"
        )

    async def fetch_current_ad(self) -> tuple[list[OfferData], AdMetadata]:
        """Fetch Woodman's weekly ad via page scrape + OCR."""
        metadata = AdMetadata(
            period_start=date.today(),
            period_end=date.today() + timedelta(days=6),
            store_location=self.zip_or_store_id,
        )

        try:
            ad_links = await self._find_ad_links()
            if not ad_links:
                logger.warning("woodmans: no ad links found on page")
                return [], metadata

            all_offers: list[OfferData] = []
            partial = False
            for link in ad_links[:3]:  # limit to 3 documents
                try:
                    text, conf = await self._ocr_link(link)
                    if conf < self.OCR_CONFIDENCE_THRESHOLD:
                        partial = True
                    offers = self._parse_ocr_text(text)
                    all_offers.extend(offers)
                except Exception as exc:  # pragma: no cover
                    logger.error("woodmans: OCR failed for %s: %s", link, exc)
                    partial = True

            if not all_offers:
                logger.warning("woodmans: no offers parsed from OCR")
                return [], metadata

            meta = AdMetadata(
                period_start=date.today(),
                period_end=date.today() + timedelta(days=6),
                store_location=self.zip_or_store_id,
                raw_payload_ref="partial" if partial else "",
            )
            return all_offers, meta

        except Exception as exc:  # pragma: no cover
            logger.error("woodmans: fetch_current_ad failed: %s", exc)
            return [], metadata

    # ----------------------------------------------------------- link discovery

    async def _find_ad_links(self) -> list[str]:
        """Scrape the Woodman's ads page for PDF/image links."""
        if not await self.check_robots_txt(WOODMANS_ADS_URL):
            logger.info("woodmans: robots.txt disallows %s", WOODMANS_ADS_URL)
            return []

        # Try browserless first for JS-rendered page
        from .base import BrowserClient

        html: str | None = None
        try:
            bc = BrowserClient()
            html = await bc.render_page(WOODMANS_ADS_URL)
        except Exception as exc:
            logger.debug("woodmans: browserless unavailable: %s", exc)

        if not html:
            resp = await self.fetch_with_retry(WOODMANS_ADS_URL)
            if resp is None:
                return []
            html = resp.text

        self.save_raw_payload({"html": html[:50000]}, suffix="_page")

        # Find PDF and image links
        links = re.findall(
            r'href="([^"]+\.(?:pdf|png|jpg|jpeg))"', html, re.IGNORECASE
        )
        # Also match src= for images
        links += re.findall(
            r'src="([^"]+\.(?:png|jpg|jpeg))"', html, re.IGNORECASE
        )
        # De-dup, make absolute
        seen = set()
        absolute = []
        for link in links:
            if link.startswith("http"):
                url = link
            elif link.startswith("/"):
                url = "https://www.woodmans-food.com" + link
            else:
                continue
            if url not in seen:
                seen.add(url)
                absolute.append(url)
        logger.info("woodmans: found %d ad links", len(absolute))
        return absolute

    # ----------------------------------------------------------- OCR

    async def _ocr_link(self, url: str) -> tuple[str, float]:
        """Download the PDF/image and run it through the tesseract service.

        Returns ``(text, confidence)`` where confidence is 0–100.
        """
        # Download the document
        resp = await self.fetch_with_retry(url)
        if resp is None:
            return "", 0.0
        content = resp.content
        self.save_raw_payload(
            {"url": url, "size_bytes": len(content)}, suffix="_docmeta"
        )

        # Send to tesseract OCR service
        # The service accepts multipart file upload and returns JSON
        import httpx

        await self.rate_limit()
        async with httpx.AsyncClient(timeout=120) as client:
            files = {"file": ("ad", content, "application/octet-stream")}
            try:
                ocr_resp = await client.post(self.tesseract_url, files=files)
                if ocr_resp.status_code != 200:
                    logger.warning(
                        "woodmans: OCR service returned %d", ocr_resp.status_code
                    )
                    return "", 0.0
                ocr_data = ocr_resp.json()
            except Exception as exc:
                logger.error("woodmans: OCR request failed: %s", exc)
                return "", 0.0

        text = ocr_data.get("text", "")
        confidence = float(ocr_data.get("confidence", 0))
        logger.info(
            "woodmans: OCR %s → %d chars, %.1f%% conf",
            url,
            len(text),
            confidence,
        )
        return text, confidence

    # ----------------------------------------------------------- parse OCR text

    def _parse_ocr_text(self, text: str) -> list[OfferData]:
        """Structure OCR text into offers using regex patterns."""
        offers: list[OfferData] = []
        if not text:
            return offers

        for line in text.splitlines():
            line = line.strip()
            if len(line) < 4:
                continue
            # Try structured offer line: "Product Name $1.99 /lb"
            m = _OFFER_LINE_RE.match(line)
            if m:
                name = m.group(1).strip()
                price = self.dollars_to_cents(m.group(2))
                unit = m.group(3)
                if not price or not name:
                    continue
                size_text = ""
                unit_price_unknown = True
                effective_unit_price = 0
                if unit:
                    size_text = f"/{unit.lower()}"
                    # If price is per-unit, effective_unit_price = price
                    effective_unit_price = price
                    unit_price_unknown = False
                offers.append(
                    OfferData(
                        raw_text=line,
                        product_name=name,
                        price=price,
                        deal_type="sale",
                        size_text=size_text,
                        effective_unit_price=effective_unit_price,
                        unit_price_unknown=unit_price_unknown,
                    )
                )
                continue

            # Fallback: just find a price in the line
            pm = _PRICE_RE.search(line)
            if pm and len(line) > 6:
                price = self.dollars_to_cents(pm.group(1))
                # Use the text before the price as the name
                name = line[: pm.start()].strip().rstrip("-–—:").strip()
                if name and price:
                    # Try to find size info
                    sm = _SIZE_RE.search(line)
                    size_text = sm.group(0) if sm else ""
                    offers.append(
                        OfferData(
                            raw_text=line,
                            product_name=name,
                            price=price,
                            deal_type="sale",
                            size_text=size_text,
                        )
                    )
        return offers
