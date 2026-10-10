"""Set up Supabase from the terminal.

    cd backend
    python -m scripts.setup_supabase check        # connect to the database, print version + tables
    python -m scripts.setup_supabase migrate      # apply backend/migrations/*.sql
    python -m scripts.setup_supabase seed-users   # create/update users + roles via the Auth Admin API
    python -m scripts.setup_supabase all          # check -> migrate -> seed-users

Needs backend/.env: DATABASE_URL always; SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and
SEED_USER_PASSWORD for seed-users. Every step is idempotent (safe to re-run).
Role and location go into app_metadata, which only the service key can write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import psycopg

from app.config import get_settings
from app.db_migrate import migrate

USERS_FILE = Path(__file__).with_name("seed_users.json")
EXPECTED_TABLES = {"ingestion_batches", "assets", "liabilities", "historical_log", "ingestion_errors", "audit_log"}


def check_database(url: str) -> dict:
    with psycopg.connect(url, connect_timeout=10, prepare_threshold=None) as conn:
        version = conn.execute("select version()").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'public'")}
        rls = dict(conn.execute(
            "select relname, relrowsecurity from pg_class where relkind = 'r' and relnamespace = 'public'::regnamespace"
        ).fetchall())
    return {"version": version.split(",")[0], "present": sorted(EXPECTED_TABLES & tables),
            "missing": sorted(EXPECTED_TABLES - tables), "rls_off": sorted(t for t in EXPECTED_TABLES & tables if not rls.get(t))}


def _admin_headers(service_key: str) -> dict:
    # New-style secret keys (sb_secret_...) go in `apikey` only; legacy service_role JWTs also work as Bearer.
    h = {"apikey": service_key}
    if not service_key.startswith("sb_"):
        h["Authorization"] = f"Bearer {service_key}"
    return h


def seed_users(supabase_url: str, service_key: str, password: str, users: list[dict],
               transport: httpx.BaseTransport | None = None) -> list[tuple[str, str]]:
    """Create each user (email pre-confirmed) or update the role/location of an existing one."""
    results: list[tuple[str, str]] = []
    with httpx.Client(base_url=f"{supabase_url.rstrip('/')}/auth/v1", headers=_admin_headers(service_key),
                      timeout=20, transport=transport) as c:
        existing: dict[str, str] = {}
        page = 1
        while True:                                      # paginate: do not assume < 50 users
            r = c.get("/admin/users", params={"page": page, "per_page": 200})
            r.raise_for_status()
            batch = r.json().get("users", [])
            existing.update({(u.get("email") or "").lower(): u["id"] for u in batch})
            if len(batch) < 200:
                break
            page += 1
        for u in users:
            meta = {"role": u["role"], "location": u.get("location")}
            uid = existing.get(u["email"].lower())
            if uid:
                r = c.put(f"/admin/users/{uid}", json={"app_metadata": meta})
                action = "updated"
            else:
                r = c.post("/admin/users", json={"email": u["email"], "password": password,
                                                 "email_confirm": True, "app_metadata": meta})
                action = "created"
            if r.status_code >= 400:
                raise SystemExit(f"{u['email']}: Supabase said {r.status_code}: {r.text[:200]}")
            results.append((u["email"], action))
    return results


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "all"
    if cmd not in ("check", "migrate", "seed-users", "all"):
        print(__doc__)
        return 2
    s = get_settings()
    if cmd in ("check", "all"):
        info = check_database(s.database_url)
        print(f"connected: {info['version']}")
        print(f"tables present: {', '.join(info['present']) or 'none yet'}")
        if info["missing"] and cmd == "check":
            print(f"missing: {', '.join(info['missing'])}  -> run: python -m scripts.setup_supabase migrate")
    if cmd in ("migrate", "all"):
        migrate(s.database_url)
        info = check_database(s.database_url)
        print(f"after migrate, missing tables: {info['missing'] or 'none'}; RLS off on: {info['rls_off'] or 'none'}")
        if info["missing"] or info["rls_off"]:
            return 1
    if cmd in ("seed-users", "all"):
        missing = [n for n, v in [("SUPABASE_URL", s.supabase_url), ("SUPABASE_SERVICE_ROLE_KEY", s.supabase_service_role_key),
                                  ("SEED_USER_PASSWORD", s.seed_user_password)] if not v]
        if missing:
            print(f"skipping seed-users: set {', '.join(missing)} in backend/.env")
            return 1 if cmd == "seed-users" else 0
        for email, action in seed_users(s.supabase_url, s.supabase_service_role_key, s.seed_user_password,
                                        json.loads(USERS_FILE.read_text())):
            print(f"{action:8} {email}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (psycopg.Error, httpx.HTTPError) as e:
        print(f"Failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        sys.exit(1)
