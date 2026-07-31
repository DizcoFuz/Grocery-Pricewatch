"""Smoke test for the conftest harness — verifies the TestClient boots."""


def test_client_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_client_seeds_stores(client):
    r = client.get("/api/stores")
    assert r.status_code == 200
    stores = r.json()
    assert len(stores) == 7  # spec §1.2 default stores


def test_db_session_direct(db):
    from app.crud import get_stores
    stores = get_stores(db)
    assert len(stores) == 7
