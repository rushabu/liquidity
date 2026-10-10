"""Shared fixtures. Integration tests need a throw-away Postgres:
    export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/liquid_test
The test database is dropped and re-migrated for every test session."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql://x:x@localhost:1/none"))
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ["DEV_AUTH_BYPASS"] = "false"
os.environ.setdefault("ENVIRONMENT", "test")      # no background AI warm-up during tests

DATA = Path(__file__).parent / "data"
TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def engine():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from sqlalchemy import create_engine, text
    from app.db import sqlalchemy_url
    from app.db_migrate import migrate
    eng = create_engine(sqlalchemy_url(TEST_DB), connect_args={"prepare_threshold": None})
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public"))
    migrate(TEST_DB)
    return eng
