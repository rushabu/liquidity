import json

import httpx

from scripts.setup_supabase import _admin_headers, seed_users

USERS = [{"email": "support@liquidplus.dev", "role": "support", "location": None},
         {"email": "Singapore@liquidplus.dev", "role": "standard", "location": "Singapore"}]


def fake_supabase(existing, calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None, dict(req.headers)))
        if req.method == "GET":
            return httpx.Response(200, json={"users": existing})
        return httpx.Response(200, json={"id": "new"})
    return httpx.MockTransport(handler)


def test_creates_missing_users_confirmed_with_role_in_app_metadata():
    calls = []
    out = seed_users("https://x.supabase.co/", "svc", "pw", USERS, fake_supabase([], calls))
    assert out == [("support@liquidplus.dev", "created"), ("Singapore@liquidplus.dev", "created")]
    post = [c for c in calls if c[0] == "POST"]
    assert post[0][1] == "/auth/v1/admin/users"
    assert post[0][2] == {"email": "support@liquidplus.dev", "password": "pw", "email_confirm": True,
                          "app_metadata": {"role": "support", "location": None}}
    assert post[1][2]["app_metadata"] == {"role": "standard", "location": "Singapore"}
    assert "user_metadata" not in json.dumps(post)           # roles must never go in user_metadata


def test_updates_existing_users_case_insensitively_without_resetting_password():
    calls = []
    existing = [{"id": "u1", "email": "SUPPORT@liquidplus.dev"}]
    out = seed_users("https://x.supabase.co", "svc", "pw", USERS, fake_supabase(existing, calls))
    assert out[0] == ("support@liquidplus.dev", "updated") and out[1][1] == "created"
    put = [c for c in calls if c[0] == "PUT"][0]
    assert put[1] == "/auth/v1/admin/users/u1" and put[2] == {"app_metadata": {"role": "support", "location": None}}


def test_failure_from_supabase_is_reported_not_swallowed():
    def handler(req):
        return httpx.Response(200, json={"users": []}) if req.method == "GET" else httpx.Response(422, text="weak password")
    try:
        seed_users("https://x.supabase.co", "svc", "1", USERS, httpx.MockTransport(handler))
        assert False, "should have raised"
    except SystemExit as e:
        assert "422" in str(e) and "weak password" in str(e)


def test_key_styles():
    assert _admin_headers("sb_secret_abc") == {"apikey": "sb_secret_abc"}
    assert _admin_headers("eyJ.legacy.jwt") == {"apikey": "eyJ.legacy.jwt", "Authorization": "Bearer eyJ.legacy.jwt"}
