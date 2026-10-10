"""An unreachable database must give a clear 503, not a hang or a generic 500 (no DB needed)."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_engine


@pytest.fixture
def dead_db(monkeypatch):
    monkeypatch.setattr(get_settings(), "database_url", "postgresql://x:x@127.0.0.1:1/none")   # nothing listens
    monkeypatch.setattr(get_settings(), "dev_auth_bypass", True)
    monkeypatch.setattr(get_settings(), "db_connect_timeout", 2)       # psycopg's minimum; keeps the test quick
    get_engine.cache_clear()
    yield
    get_engine.cache_clear()


def test_engine_sets_a_connect_timeout(monkeypatch):
    import app.db as db
    seen = {}
    monkeypatch.setattr(db, "create_engine", lambda url, **kw: seen.update(kw) or object())
    db.get_engine.cache_clear()
    try:
        db.get_engine()
    finally:
        db.get_engine.cache_clear()
    assert seen["connect_args"]["connect_timeout"] == get_settings().db_connect_timeout > 0


@pytest.mark.parametrize("path", ["/api/ingest/summary", "/api/ladder"])
def test_unreachable_database_is_503_with_correlation_id(dead_db, path):
    from app.main import app
    r = TestClient(app).get(path)
    assert r.status_code == 503, r.text
    err = r.json()["error"]
    assert err["code"] == "DATABASE_UNAVAILABLE" and err["details"]["correlation_id"] == r.headers["x-request-id"]
    assert "127.0.0.1" not in r.text                     # no connection details leaked


def test_health_db_reports_down(dead_db):
    from app.main import app
    r = TestClient(app).get("/health/db")
    assert r.status_code == 503 and r.json()["database"] == "unreachable"
