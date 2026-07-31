"""Shared pytest fixtures for the Grocery Pricewatch test suite.

Each test gets a fresh TestClient backed by an isolated SQLite DB in a
temporary directory, so tests never touch the real /data/grocery.db and
never interfere with each other.

Key design points:
- We do NOT ``importlib.reload`` the models module (that would re-register
  ORM classes on the shared declarative ``Base`` and emit "already
  contains a class" warnings).  Instead we patch ``database.engine`` and
  ``database.SessionLocal`` in place so the existing ``Base.metadata``
  (which the models registered on at first import) keeps its tables and
  we just bind them to a fresh SQLite file.
- We force ``TZ=UTC`` so APScheduler's timezone detection (which calls
  ``datetime.astimezone().tzname()`` and may return an abbreviation like
  ``CDT`` that ``zoneinfo.ZoneInfo`` rejects) works on every host.
"""

from __future__ import annotations

import importlib
import os

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def _make_engine(db_url: str):
    """Build a SQLite engine with WAL + FK pragmas, matching database.py."""
    eng = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        echo=False,
        future=True,
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        import sqlite3  # noqa: F401
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return eng


@pytest.fixture(scope="function", autouse=True)
def _force_utc_timezone(monkeypatch):
    """Force UTC so the APScheduler startup in the app lifespan doesn't
    blow up on systems whose local tzname() returns an abbreviation
    (e.g. ``CDT``) that ``zoneinfo.ZoneInfo`` rejects.
    """
    monkeypatch.setenv("TZ", "UTC")


@pytest.fixture(scope="function")
def _fresh_db(tmp_path):
    """Internal: rebind the database module to a tmp SQLite file and
    create tables.  Yields the patched database module.
    """
    db_dir = str(tmp_path)
    db_path = os.path.join(db_dir, "grocery.db")
    db_url = f"sqlite:///{db_path}"
    os.environ["GROCERY_DB_DIR"] = db_dir

    from app import database as dbmod

    new_engine = _make_engine(db_url)
    new_session = sessionmaker(
        bind=new_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )

    # Patch in place so every module that does `from app.database import X`
    # and calls X at runtime (e.g. get_db → SessionLocal()) picks up the
    # new factory.  Keep references to restore afterwards.
    old_engine = dbmod.engine
    old_session = dbmod.SessionLocal
    old_url = dbmod.DATABASE_URL
    dbmod.engine = new_engine
    dbmod.SessionLocal = new_session
    dbmod.DATABASE_URL = db_url

    # Create tables on the existing Base.metadata (models already registered).
    dbmod.init_db()

    # Seed the default stores + settings so tests start from the same
    # baseline the app's lifespan creates.  (The client fixture re-runs
    # the lifespan which re-seeds, but seed_stores is idempotent — it
    # only inserts when the table is empty.)
    from app import seed as _seed_mod
    _seed_session = new_session()
    try:
        _seed_mod.seed_stores(_seed_session)
        _seed_mod.seed_default_settings(_seed_session)
    finally:
        _seed_session.close()

    yield dbmod

    new_engine.dispose()
    dbmod.engine = old_engine
    dbmod.SessionLocal = old_session
    dbmod.DATABASE_URL = old_url
    os.environ.pop("GROCERY_DB_DIR", None)


@pytest.fixture(scope="function")
def client(_fresh_db):
    """Fresh TestClient with isolated SQLite DB for each test.

    We reload ``app.main`` so its lifespan + route closures re-bind to
    the patched ``database.SessionLocal`` / ``database.engine``.
    """
    from app import main as _main_mod
    importlib.reload(_main_mod)
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def db(_fresh_db):
    """A SQLAlchemy session sharing the client's isolated DB.

    Use this to insert/mutate data directly and then hit the API via the
    *client* fixture to verify endpoints read the same rows.
    """
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
