"""Login with predefined credentials against a mocked Supabase Auth, and role/location access control."""
import json
import time

import httpx
import jwt
import pytest
from sqlalchemy import text

from tests.integration.conftest import auth, upload

USERS = {  # email -> (password, app_metadata)
    "support@liquidplus.dev": ("pw-support", {"role": "support", "location": None}),
    "singapore@liquidplus.dev": ("pw-sg", {"role": "standard", "location": "Singapore"}),
    "norole@liquidplus.dev": ("pw-x", {}),
}


def _issued(email: str, meta: dict) -> dict:
    import os
    claims = {"sub": f"id-{email}", "email": email, "aud": "authenticated", "exp": int(time.time()) + 3600,
              "role": "authenticated", "app_metadata": meta}
    return {"access_token": jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256"),
            "refresh_token": f"refresh-{email}", "expires_in": 3600, "token_type": "bearer"}


def fake_supabase(request: httpx.Request) -> httpx.Response:
    assert request.headers["apikey"] == "sb_publishable_test"
    body = json.loads(request.content)
    grant = request.url.params["grant_type"]
    if grant == "password":
        pw, meta = USERS.get(body["email"], (None, None))
        if pw is None or pw != body["password"]:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Invalid login credentials"})
        return httpx.Response(200, json=_issued(body["email"], meta))
    email = body["refresh_token"].removeprefix("refresh-")
    if email in USERS:
        return httpx.Response(200, json=_issued(email, USERS[email][1]))
    return httpx.Response(400, json={"error": "invalid_grant"})


@pytest.fixture(autouse=True)
def supabase(monkeypatch):
    from app.config import get_settings
    from app.routers import auth as auth_router
    s = get_settings()
    monkeypatch.setattr(s, "supabase_url", "https://example.supabase.co")
    monkeypatch.setattr(s, "supabase_anon_key", "sb_publishable_test")
    monkeypatch.setattr(auth_router, "transport", httpx.MockTransport(fake_supabase))
    yield


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_returns_session_and_profile_and_is_audited(client, db):
    r = login(client, "  Singapore@LiquidPlus.dev ", "pw-sg")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["refresh_token"] == "refresh-singapore@liquidplus.dev" and s["expires_at"] > time.time()
    assert s["user"] == {"id": "id-singapore@liquidplus.dev", "email": "singapore@liquidplus.dev", "role": "standard",
                         "location": "Singapore", "scope": "Singapore", "can_upload": False}
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {s['access_token']}"}).json()
    assert me == s["user"]
    sup = login(client, "support@liquidplus.dev", "pw-support").json()["user"]
    assert sup["scope"] == "All locations" and sup["can_upload"] is True
    rows = db.execute(text("select actor, action from audit_log where action like 'LOGIN%' order by id")).all()
    assert [tuple(r) for r in rows] == [("singapore@liquidplus.dev", "LOGIN"), ("support@liquidplus.dev", "LOGIN")]


def test_wrong_password_unknown_user_and_no_role(client, db):
    for email, pw in (("singapore@liquidplus.dev", "nope"), ("ghost@liquidplus.dev", "x")):
        r = login(client, email, pw)
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert r.json()["error"]["message"] == "Wrong email or password."          # never says which one was wrong
    r = login(client, "norole@liquidplus.dev", "pw-x")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NO_ROLE"
    assert db.execute(text("select count(*) from audit_log where action = 'LOGIN_FAILED'")).scalar_one() == 3
    assert client.post("/api/auth/login", json={"email": "a@b.c"}).status_code == 422


def test_refresh_and_expired_session(client):
    r = client.post("/api/auth/refresh", json={"refresh_token": "refresh-support@liquidplus.dev"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "support"
    r = client.post("/api/auth/refresh", json={"refresh_token": "refresh-unknown-token"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "SESSION_EXPIRED"


def test_supabase_down_or_not_configured(client, monkeypatch):
    from app.config import get_settings
    from app.routers import auth as auth_router

    def down(_):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(auth_router, "transport", httpx.MockTransport(down))
    r = login(client, "support@liquidplus.dev", "pw-support")
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    monkeypatch.setattr(get_settings(), "supabase_anon_key", "")
    r = login(client, "support@liquidplus.dev", "pw-support")
    assert r.status_code == 500 and r.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_rbac_standard_user_is_scoped_support_sees_all(client):
    for b in ("asset", "liability"):
        assert upload(client, b).status_code == 201
    sg = {"Authorization": f"Bearer {login(client, 'singapore@liquidplus.dev', 'pw-sg').json()['access_token']}"}
    ladder = client.get("/api/ladder", headers=sg).json()
    assert {r["location"] for r in ladder["ladder"]} <= {"Singapore", None}
    london = client.get("/api/ladder/transactions", params={"location": "London"}, headers=auth())
    assert london.status_code == 200 and london.json()["total"] > 0                  # support: London has data
    r = client.get("/api/ladder/transactions", params={"location": "London"}, headers=sg)
    assert r.status_code == 200 and r.json()["total"] == 0                           # Singapore user: none of it
    assert upload(client, "asset", headers=sg).status_code == 403                    # only support uploads
    everyone = {r["location"] for r in client.get("/api/ladder", headers=auth()).json()["ladder"]} - {None}
    assert everyone == {"Hong Kong", "New York", "London", "Singapore", "Paris"}
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
