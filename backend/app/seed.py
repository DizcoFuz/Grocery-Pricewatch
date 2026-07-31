"""Seed the database with the 7 default stores from spec §1.2.

Store chains with adapter_key and default ZIP:
- Aldi, Walmart, Jewel-Osco, Mariano's, Woodman's, Whole Foods, Target
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Store


# Default stores: (name, adapter_key, zip_or_store_id, ad_flip_day)
# ad_flip_day is the day of the week the store's weekly ad changes.
# Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
DEFAULT_STORES: list[dict] = [
    {
        "name": "Aldi",
        "adapter_key": "aldi",
        "zip_or_store_id": "60601",
        "ad_flip_day": 2,  # Wednesday
    },
    {
        "name": "Walmart",
        "adapter_key": "walmart",
        "zip_or_store_id": "60601",
        "ad_flip_day": 5,  # Saturday
    },
    {
        "name": "Jewel-Osco",
        "adapter_key": "jewel_osco",
        "zip_or_store_id": "60601",
        "ad_flip_day": 2,  # Wednesday
    },
    {
        "name": "Mariano's",
        "adapter_key": "marianos",
        "zip_or_store_id": "60601",
        "ad_flip_day": 2,  # Wednesday
    },
    {
        "name": "Woodman's",
        "adapter_key": "woodmans",
        "zip_or_store_id": "60601",
        "ad_flip_day": 2,  # Wednesday
    },
    {
        "name": "Whole Foods",
        "adapter_key": "whole_foods",
        "zip_or_store_id": "60601",
        "ad_flip_day": 2,  # Wednesday
    },
    {
        "name": "Target",
        "adapter_key": "target",
        "zip_or_store_id": "60601",
        "ad_flip_day": 6,  # Sunday
    },
]


def seed_stores(db: Session) -> int:
    """Create default stores if the stores table is empty.

    Returns the number of stores created.
    """
    existing = db.query(Store).count()
    if existing > 0:
        return 0
    created = 0
    for store_data in DEFAULT_STORES:
        store = Store(
            name=store_data["name"],
            adapter_key=store_data["adapter_key"],
            zip_or_store_id=store_data["zip_or_store_id"],
            enabled=True,
        )
        db.add(store)
        created += 1
    db.commit()
    return created


def seed_default_settings(db: Session) -> int:
    """Create default settings if the settings table is empty."""
    from app import crud
    from app.models import Setting

    settings = crud.get_all_settings(db)
    created = 0
    for key, value in settings.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        if existing is None:
            crud.set_setting(db, key, value)
            created += 1
    return created
