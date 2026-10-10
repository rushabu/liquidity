from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import get_settings


def sqlalchemy_url(url: str) -> str:
    """Supabase gives postgresql:// ; make SQLAlchemy use psycopg 3."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache
def get_engine() -> Engine:
    # prepare_threshold=None: server-side prepared statements are not
    # supported by Supabase's transaction pooler (port 6543); harmless elsewhere.
    s = get_settings()
    return create_engine(
        sqlalchemy_url(s.database_url),
        pool_pre_ping=True, pool_size=5, max_overflow=5,
        connect_args={"prepare_threshold": None, "connect_timeout": s.db_connect_timeout},
    )


def check_connection(engine: Engine | None = None) -> None:
    with (engine or get_engine()).connect() as conn:
        conn.execute(text("select 1"))
