"""Apply backend/migrations/*.sql in order. Run:  python -m app.db_migrate

Each file runs once (tracked in schema_migrations). The SQL itself is
idempotent as well, so re-running against an existing database is safe."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from .config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate(database_url: str | None = None, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    url = database_url or get_settings().database_url
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("create table if not exists schema_migrations "
                     "(name text primary key, applied_at timestamptz not null default now())")
        done = {r[0] for r in conn.execute("select name from schema_migrations")}
        for f in sorted(migrations_dir.glob("*.sql")):
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("insert into schema_migrations(name) values (%s)", (f.name,))
            applied.append(f.name)
            print(f"applied {f.name}")
    if not applied:
        print("database is up to date")
    return applied


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:  # clear message instead of a stack trace for setup mistakes
        print(f"Migration failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        sys.exit(1)
