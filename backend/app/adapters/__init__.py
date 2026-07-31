"""Store adapter registry for the Grocery Pricewatch app.

Importing this package makes all store adapters available and provides
:meth:`get_adapter` and :meth:`list_available_adapters`.

New stores are added by:
  1. Writing a module under ``app/adapters/`` that subclasses ``StoreAdapter``.
  2. Registering it in ``_ADAPTER_REGISTRY`` below.

Spec ref: "New stores addable by writing one adapter."
"""

from __future__ import annotations

import logging
from typing import Dict, List, Type

from .base import AdMetadata, OfferData, StoreAdapter
from .aldi import AldiAdapter
from .jewel_osco import JewelOscoAdapter
from .marianos import MarianosAdapter
from .target import TargetAdapter
from .whole_foods import WholeFoodsAdapter
from .walmart import WalmartAdapter
from .woodmans import WoodmansAdapter

logger = logging.getLogger(__name__)

# Maps adapter key -> adapter class
_ADAPTER_REGISTRY: Dict[str, Type[StoreAdapter]] = {
    "aldi": AldiAdapter,
    "walmart": WalmartAdapter,
    "jewel_osco": JewelOscoAdapter,
    "marianos": MarianosAdapter,
    "woodmans": WoodmansAdapter,
    "whole_foods": WholeFoodsAdapter,
    "target": TargetAdapter,
}


def get_adapter(adapter_key: str) -> Type[StoreAdapter]:
    """Return the adapter class for ``adapter_key``.

    Raises ``KeyError`` if the key is unknown.
    """
    if adapter_key not in _ADAPTER_REGISTRY:
        raise KeyError(
            f"Unknown adapter '{adapter_key}'. "
            f"Available: {', '.join(sorted(_ADAPTER_REGISTRY))}"
        )
    return _ADAPTER_REGISTRY[adapter_key]


def list_available_adapters() -> List[str]:
    """Return a sorted list of all registered adapter keys."""
    return sorted(_ADAPTER_REGISTRY.keys())


__all__ = [
    "AdMetadata",
    "OfferData",
    "StoreAdapter",
    "get_adapter",
    "list_available_adapters",
]
