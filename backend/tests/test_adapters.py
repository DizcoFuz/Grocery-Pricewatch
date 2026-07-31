"""Fixture-based parser tests for store adapters (R-1).

Each test loads a mock JSON fixture (recorded to match the real API response
format as closely as possible from reading the adapter source code) and feeds
it through the adapter's parsing logic with the HTTP client mocked, so no
real network calls are made.

Fixtures live under ``tests/fixtures/`` and are named ``{store}_mock_fixture.json``.
They are mock-based — no adapter has been verified against a live store endpoint
yet (see the "Verified" column in the README adapter table).  When a live
fetch is eventually performed, replace the mock fixture with the sanitized
real response and drop the ``_mock_`` infix.

Shared assertions for every adapter:
  - At least 1 offer is parsed
  - Each offer has non-empty ``product_name`` or ``raw_text``
  - Each offer has ``price > 0``
  - ``deal_type`` is set to a non-empty value
  - ``AdMetadata`` has ``period_start`` and ``period_end``
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --------------------------------------------------------------------------- #
# Paths / helpers
# --------------------------------------------------------------------------- #

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from ``tests/fixtures/``."""
    path = os.path.join(FIXTURES_DIR, name)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def make_mock_response(json_data: Any, text: str | None = None) -> MagicMock:
    """Build a fake ``httpx.Response``-like object."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = json_data
    mock_resp.text = text if text is not None else json.dumps(json_data)
    mock_resp.content = mock_resp.text.encode("utf-8")
    return mock_resp


def flipp_fetch_router(fixture: dict[str, Any]):
    """Return an AsyncMock that routes Flipp's two-step fetch.

    The Flipp mixin calls ``fetch_with_retry`` twice:
      1. ``/flipp/flyers``  → returns the flyers list
      2. ``/flipp/flyers/{id}`` → returns the flyer detail (items)

    The mock inspects the URL and returns the corresponding fixture section.
    """

    async def _side_effect(url: str, **kwargs: Any):
        if "/flyers" == url.split("backflipp.wishabi.com")[-1].split("?")[0] or url.endswith("/flyers"):
            # flyers list endpoint
            flyers = fixture.get("flyers_response", [])
            return make_mock_response(flyers)
        # flyer detail endpoint: /flipp/flyers/{id}
        # Extract the id from the URL
        parts = url.rstrip("/").split("/")
        flyer_id = parts[-1].split("?")[0]  # strip query params
        details_map = fixture.get("flyer_details", {})
        detail = details_map.get(flyer_id)
        if detail is None:
            # Fall back to first available detail
            detail = next(iter(details_map.values()), {})
        return make_mock_response(detail)

    return AsyncMock(side_effect=_side_effect)


# Shared assertion helpers -------------------------------------------------- #

VALID_DEAL_TYPES = {
    "sale", "rollback", "2_for", "bogo", "circle", "prime",
}


def assert_offers_valid(offers: list, min_count: int = 1) -> None:
    """Shared assertions on a list of OfferData."""
    assert len(offers) >= min_count, f"Expected >= {min_count} offers, got {len(offers)}"
    for i, o in enumerate(offers):
        assert o.price > 0, f"Offer {i} has price <= 0: {o.price}"
        assert o.product_name or o.raw_text, f"Offer {i} has no product_name or raw_text"
        assert o.deal_type, f"Offer {i} has empty deal_type"
        assert o.deal_type in VALID_DEAL_TYPES, (
            f"Offer {i} has invalid deal_type '{o.deal_type}'"
        )


def assert_meta_valid(meta) -> None:
    """Shared assertions on AdMetadata."""
    assert meta is not None
    assert meta.period_start is not None, "AdMetadata.period_start is None"
    assert meta.period_end is not None, "AdMetadata.period_end is None"
    assert isinstance(meta.period_start, date), "period_start is not a date"
    assert isinstance(meta.period_end, date), "period_end is not a date"


# --------------------------------------------------------------------------- #
# Flipp-based adapters: Aldi, Jewel-Osco, Mariano's, Walmart, Target
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_aldi_parses_fixture():
    """Aldi adapter correctly parses a Flipp-format mock fixture."""
    from app.adapters.aldi import AldiAdapter

    fixture = load_fixture("aldi_mock_fixture.json")
    adapter = AldiAdapter("aldi", "60601")

    with patch.object(
        adapter, "fetch_with_retry", new=flipp_fetch_router(fixture)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # Aldi fixture has 5 real items (2 skipped: display_type=5 banner, ttm_url promo)
    assert len(offers) == 5


@pytest.mark.asyncio
async def test_jewel_osco_parses_fixture():
    """Jewel-Osco adapter correctly parses a Flipp-format mock fixture."""
    from app.adapters.jewel_osco import JewelOscoAdapter

    fixture = load_fixture("jewel_osco_mock_fixture.json")
    adapter = JewelOscoAdapter("jewel_osco", "60601")

    with patch.object(
        adapter, "fetch_with_retry", new=flipp_fetch_router(fixture)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # 5 real items, 1 skipped (display_type=5 banner)
    assert len(offers) == 5


@pytest.mark.asyncio
async def test_marianos_parses_fixture():
    """Mariano's adapter correctly parses a Flipp-format mock fixture."""
    from app.adapters.marianos import MarianosAdapter

    fixture = load_fixture("marianos_mock_fixture.json")
    adapter = MarianosAdapter("marianos", "60601")

    with patch.object(
        adapter, "fetch_with_retry", new=flipp_fetch_router(fixture)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # 5 real items, 1 skipped (display_type=5 banner)
    assert len(offers) == 5


@pytest.mark.asyncio
async def test_walmart_parses_fixture():
    """Walmart adapter correctly parses a Flipp-format fixture and filters to grocery."""
    from app.adapters.walmart import WalmartAdapter

    fixture = load_fixture("walmart_mock_fixture.json")
    adapter = WalmartAdapter("walmart", "60601")

    with patch.object(
        adapter, "fetch_with_retry", new=flipp_fetch_router(fixture)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # All fixture items contain grocery keywords (milk, bread, eggs, chicken, rice, detergent)
    # 6 real items, 1 skipped (display_type=5)
    assert len(offers) == 6


@pytest.mark.asyncio
async def test_target_parses_fixture():
    """Target adapter parses Flipp weekly ad + RedSky Circle offers from fixture."""
    from app.adapters.target import TargetAdapter

    fixture = load_fixture("target_mock_fixture.json")
    adapter = TargetAdapter("target", "60601")

    # Target calls fetch_with_retry for: Flipp flyers, Flipp detail, and Circle API
    circle_data = fixture.get("circle_response", {})

    async def _side_effect(url: str, **kwargs: Any):
        if "redsky.target.com" in url or "circle" in url.lower():
            return make_mock_response(circle_data)
        # Flipp flyers list endpoint: .../flipp/flyers (exact, no trailing path)
        path_after_host = url.split("backflipp.wishabi.com")[-1].split("?")[0]
        if path_after_host == "/flipp/flyers":
            flyers = fixture.get("flyers_response", [])
            return make_mock_response(flyers)
        # Flipp flyer detail endpoint: /flipp/flyers/{id}
        if "/flipp/flyers/" in path_after_host:
            flyer_id = path_after_host.rstrip("/").split("/")[-1]
            details_map = fixture.get("flyer_details", {})
            detail = details_map.get(flyer_id) or next(iter(details_map.values()), {})
            return make_mock_response(detail)
        # Target weekly ad HTML fallback — return empty HTML (no offers from this path)
        return make_mock_response({}, text="<html><body></body></html>")

    with patch.object(
        adapter, "fetch_with_retry", new=AsyncMock(side_effect=_side_effect)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # 3 Flipp items + 4 Circle offers = 7 total
    assert len(offers) == 7
    # At least one circle offer
    circle_offers = [o for o in offers if o.deal_type == "circle"]
    assert len(circle_offers) >= 1
    # Circle offers require membership/coupon
    assert all(o.requires_membership_or_coupon for o in circle_offers)


# --------------------------------------------------------------------------- #
# Whole Foods — __NEXT_DATA__ HTML parse
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_whole_foods_parses_fixture():
    """Whole Foods adapter parses a __NEXT_DATA__ sales-flyer fixture."""
    from app.adapters.whole_foods import WholeFoodsAdapter

    fixture = load_fixture("whole_foods_mock_fixture.json")

    # Build a fake HTML page with embedded __NEXT_DATA__ JSON
    next_data_json = json.dumps(fixture["next_data"])
    valid_text = fixture.get("valid_dates_text", "")
    html = (
        f'<!DOCTYPE html><html><head>'
        f'<script id="__NEXT_DATA__" type="application/json">{next_data_json}</script>'
        f'</head><body>{valid_text}</body></html>'
    )

    adapter = WholeFoodsAdapter("whole_foods", "60601")

    # Mock robots check (return True = allowed), BrowserClient (return our HTML),
    # and save_raw_payload (no-op).  We patch BrowserClient at both the module
    # where it's defined and where it's imported, and also patch
    # fetch_with_retry as a fallback in case the adapter falls through to it.
    mock_bc = MagicMock()
    mock_bc.render_page = AsyncMock(return_value=html)
    mock_bc.screenshot = AsyncMock(return_value=None)

    mock_html_resp = MagicMock()
    mock_html_resp.status_code = 200
    mock_html_resp.text = html

    with patch.object(
        adapter, "check_robots_txt", new=AsyncMock(return_value=True)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ), patch(
        "app.adapters.base.BrowserClient", return_value=mock_bc
    ), patch(
        "app.adapters.whole_foods.BrowserClient", return_value=mock_bc,
        create=True,
    ), patch.object(
        adapter, "fetch_with_retry", new=AsyncMock(return_value=mock_html_resp)
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # 5 sale items + 3 prime items (strawberries, milk, ground beef have primePrice)
    # = 8 total
    assert len(offers) == 8
    # At least one prime offer
    prime_offers = [o for o in offers if o.deal_type == "prime"]
    assert len(prime_offers) >= 1
    assert all(o.requires_membership_or_coupon for o in prime_offers)
    # Period should be extracted from "Valid 07/24 - 07/30"
    assert meta.period_start is not None
    assert meta.period_end is not None


# --------------------------------------------------------------------------- #
# Woodman's — OCR text parse
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_woodmans_parses_fixture():
    """Woodman's adapter parses OCR-extracted text from a mock fixture."""
    from app.adapters.woodmans import WoodmansAdapter

    fixture = load_fixture("woodmans_mock_fixture.json")
    ocr_text = fixture["ocr_text"]
    confidence = fixture["confidence"]

    adapter = WoodmansAdapter("woodmans", "60601")

    # Mock the link discovery + OCR steps
    ad_links = ["https://www.woodmans-food.com/weekly-ad/page1.pdf"]

    async def _mock_find_ad_links():
        return ad_links

    async def _mock_ocr_link(url: str):
        return ocr_text, confidence

    with patch.object(
        adapter, "_find_ad_links", new=AsyncMock(side_effect=_mock_find_ad_links)
    ), patch.object(
        adapter, "_ocr_link", new=AsyncMock(side_effect=_mock_ocr_link)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert_offers_valid(offers, min_count=1)
    assert_meta_valid(meta)
    # The OCR text has ~13 product lines with prices
    assert len(offers) >= 5


# --------------------------------------------------------------------------- #
# Adapter isolation / no-network tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_flipp_adapter_returns_empty_on_fetch_failure():
    """A Flipp adapter returns ([], AdMetadata) — never raises — on network failure."""
    from app.adapters.aldi import AldiAdapter

    adapter = AldiAdapter("aldi", "60601")

    with patch.object(
        adapter, "fetch_with_retry", new=AsyncMock(return_value=None)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert offers == []
    assert meta is not None


@pytest.mark.asyncio
async def test_flipp_adapter_skips_display_type_5():
    """Flipp items with display_type=5 (banner/promo) are skipped."""
    from app.adapters.aldi import AldiAdapter

    fixture = {
        "flyers_response": [
            {
                "id": 999,
                "merchant_name": "ALDI",
                "valid_from": "2024-01-01T00:00:00Z",
                "valid_to": "2030-12-31T23:59:59Z",
            }
        ],
        "flyer_details": {
            "999": {
                "id": 999,
                "flyer_items": [
                    {
                        "name": "Real Product",
                        "current_price": 1.99,
                        "display_type": 1,
                    },
                    {
                        "name": "Banner Promo",
                        "current_price": 0,
                        "display_type": 5,
                    },
                ],
            }
        },
    }

    adapter = AldiAdapter("aldi", "60601")
    with patch.object(
        adapter, "fetch_with_retry", new=flipp_fetch_router(fixture)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert len(offers) == 1
    assert offers[0].product_name == "Real Product"


@pytest.mark.asyncio
async def test_flipp_adapter_skips_ttm_url_items():
    """Flipp items with ttm_url (tap-to-merchant promos) are skipped."""
    from app.adapters.aldi import AldiAdapter

    fixture = {
        "flyers_response": [
            {
                "id": 888,
                "merchant_name": "ALDI",
                "valid_from": "2024-01-01T00:00:00Z",
                "valid_to": "2030-12-31T23:59:59Z",
            }
        ],
        "flyer_details": {
            "888": {
                "id": 888,
                "flyer_items": [
                    {
                        "name": "Coca-Cola 12-pack",
                        "current_price": 5.99,
                        "display_type": 1,
                        "ttm_url": "https://flipp.com/ttm/coca-cola",
                    },
                    {
                        "name": "Real Product",
                        "current_price": 2.99,
                        "display_type": 1,
                    },
                ],
            }
        },
    }

    adapter = AldiAdapter("aldi", "60601")
    with patch.object(
        adapter, "fetch_with_retry", new=flipp_fetch_router(fixture)
    ), patch.object(
        adapter, "save_raw_payload", return_value=""
    ):
        offers, meta = await adapter.fetch_current_ad()

    assert len(offers) == 1
    assert offers[0].product_name == "Real Product"
