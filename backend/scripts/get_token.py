"""Sign in as a Supabase user and print an access token for testing the API.

    python -m scripts.get_token support@liquidplus.dev            # prompts for the password
    python -m scripts.get_token support@liquidplus.dev --password '...'

Needs SUPABASE_URL and SUPABASE_ANON_KEY (publishable key) in backend/.env.
The token lasts about an hour. Use it with curl (Authorization: Bearer <token>), the smoke test,
or in the browser console:  sessionStorage.setItem("liquidplus.token", "<token>")
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import sys

import httpx

from app.config import get_settings


def sign_in(supabase_url: str, anon_key: str, email: str, password: str,
            transport: httpx.BaseTransport | None = None) -> str:
    with httpx.Client(timeout=20, transport=transport) as c:
        r = c.post(f"{supabase_url.rstrip('/')}/auth/v1/token", params={"grant_type": "password"},
                   headers={"apikey": anon_key}, json={"email": email, "password": password})
    if r.status_code != 200:
        raise SystemExit(f"Sign-in failed ({r.status_code}): {r.json().get('msg') or r.json().get('error_description') or r.text[:200]}")
    return r.json()["access_token"]


def claims_of(token: str) -> dict:
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("--password")
    a = ap.parse_args()
    s = get_settings()
    if not (s.supabase_url and s.supabase_anon_key):
        print("Set SUPABASE_URL and SUPABASE_ANON_KEY in backend/.env", file=sys.stderr)
        return 1
    token = sign_in(s.supabase_url, s.supabase_anon_key, a.email, a.password or getpass.getpass("Password: "))
    meta = claims_of(token).get("app_metadata", {})
    print(f"role={meta.get('role')} location={meta.get('location')}", file=sys.stderr)
    if not meta.get("role"):
        print("WARNING: no role in app_metadata. Run: python -m scripts.setup_supabase seed-users", file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
