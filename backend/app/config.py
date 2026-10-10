"""All configuration comes from environment variables / .env. No defaults for
secrets; see .env.example."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Liquid+ API"
    environment: str = "dev"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Supabase pooled Postgres connection string (sslmode=require).
    database_url: str
    # Seconds per resolved address (the Supabase pooler has several). psycopg's default is 130 s each,
    # so a blocked port would otherwise hang every request for minutes instead of failing with a 503.
    db_connect_timeout: int = 5

    # Auth: JWT with claims sub / role / location.
    # HS256 shared secret (legacy Supabase projects / the auth module), or
    # SUPABASE_URL for JWKS verification (new asymmetric signing keys).
    jwt_secret: str = ""
    supabase_url: str = ""
    jwt_audience: str = "authenticated"
    # Only used by scripts/setup_supabase.py (never by the API, never in the browser).
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""        # publishable key; used only to sign in from scripts/get_token.py
    seed_user_password: str = ""
    # Local development only. Returns a Support user without a token.
    dev_auth_bypass: bool = False

    # Ingestion
    max_upload_mb: int = 25
    errors_preview_limit: int = 50
    insert_chunk_size: int = 5000
    upload_roles: list[str] = Field(default_factory=lambda: ["support"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
