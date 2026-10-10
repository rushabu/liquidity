import os
import time
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

DATA = Path(__file__).parent.parent / "data"
FILES = {"asset": "asset_book - 10102026.xlsx", "liability": "liability_book - 10102026.xlsx",
         "historical": "historical_log - 10102026.xlsx"}
SRC = {"asset": "asset_book.xlsx", "liability": "liability_book.xlsx", "historical": "historical_log.xlsx"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def token(role="support", location=None, sub="11111111-1111-1111-1111-111111111111", email="ops@bank.test"):
    claims = {"sub": sub, "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600,
              "role": "authenticated", "app_metadata": {"role": role, "location": location}}
    return jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256")


def auth(role="support", location=None):
    return {"Authorization": f"Bearer {token(role, location)}"}


@pytest.fixture(scope="session")
def client(engine):
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as c:
        c.execute(text("truncate ingestion_batches, audit_log restart identity cascade"))
    yield


@pytest.fixture
def db(engine):
    with engine.connect() as c:
        yield c


def upload(client, book, *, name=None, content=None, headers=None, data=None):
    name = name or FILES[book]
    content = content if content is not None else (DATA / SRC[book]).read_bytes()
    return client.post(f"/api/ingest/{book}", files={"file": (name, content, XLSX)},
                       headers=headers if headers is not None else auth(), data=data or {})


def count(db, sql, **p):
    return db.execute(text(sql), p).scalar_one()
