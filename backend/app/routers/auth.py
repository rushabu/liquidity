"""Login with predefined credentials (Supabase Auth) and role-based access.

The browser never talks to Supabase: it posts email + password here, the API signs in against Supabase Auth
with the publishable key, checks that the account has a role (and a location for standard users), writes an
audit row, and returns the Supabase session. Every other endpoint verifies that access token
(app/deps.py) and scopes data by the role/location claims in app_metadata, which only the service key can set.

Roles (problem statement, task xii):
* standard - sees only the data of its assigned location (e.g. a Singapore user cannot access London data)
* support  - cross-location access: all branches, plus uploads
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import get_engine
from ..deps import CurrentUser, _decode, get_current_user
from ..services.errors import ApiError
from ..services.ingestion import _audit_best_effort

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("liquidplus.auth")

LOCATIONS = ("Hong Kong", "New York", "London", "Singapore", "Paris")
transport: httpx.BaseTransport | None = None        # tests swap in a mock Supabase


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4000)


def _profile(user: CurrentUser) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role, "location": user.location,
            "scope": "All locations" if user.is_support else user.location,
            "can_upload": user.role in get_settings().upload_roles}


def _supabase(grant: str, body: dict) -> dict:
    s = get_settings()
    if not (s.supabase_url and s.supabase_anon_key):
        raise ApiError(500, "AUTH_NOT_CONFIGURED", "Login is not configured: set SUPABASE_URL and SUPABASE_ANON_KEY "
                                                   "in backend/.env")
    try:
        with httpx.Client(timeout=15, transport=transport) as c:
            r = c.post(f"{s.supabase_url.rstrip('/')}/auth/v1/token", params={"grant_type": grant},
                       headers={"apikey": s.supabase_anon_key}, json=body)
    except httpx.HTTPError as e:
        log.warning("supabase auth unreachable: %s", e.__class__.__name__)
        raise ApiError(503, "AUTH_UNAVAILABLE", "The login service cannot be reached. Try again in a moment.") from e
    if r.status_code in (400, 401, 403, 422):
        raise ApiError(401, "INVALID_CREDENTIALS" if grant == "password" else "SESSION_EXPIRED",
                       "Wrong email or password." if grant == "password" else "Your session has expired. Sign in again.")
    if r.status_code != 200:
        log.warning("supabase auth error %s", r.status_code)
        raise ApiError(503, "AUTH_UNAVAILABLE", "The login service returned an error. Try again in a moment.")
    return r.json()


def _session(data: dict) -> tuple[dict, CurrentUser]:
    """Verify the token Supabase issued (same check as every API call) and enforce the role rules."""
    claims = _decode(data["access_token"])
    meta = claims.get("app_metadata") or {}
    user = CurrentUser(id=claims["sub"], email=claims.get("email"), role=meta.get("role") or "standard",
                       location=meta.get("location"))
    if user.role not in ("support", "standard") or (user.role == "standard" and user.location not in LOCATIONS):
        raise ApiError(403, "NO_ROLE", "This account has no role or location assigned. Ask an administrator to run "
                                       "scripts/setup_supabase seed-users.")
    expires_at = int(data.get("expires_at") or (time.time() + int(data.get("expires_in", 3600))))
    return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token"),
            "expires_at": expires_at, "user": _profile(user)}, user


@router.post("/login")
def login(body: LoginIn):
    email = body.email.strip().lower()
    try:
        session, user = _session(_supabase("password", {"email": email, "password": body.password}))
    except ApiError as e:
        if e.status in (401, 403):
            _audit_best_effort(get_engine(), email, "LOGIN_FAILED", {"code": e.code})
        raise
    _audit_best_effort(get_engine(), user.actor, "LOGIN", {"role": user.role, "location": user.location})
    log.info("login", extra={"actor": user.actor, "role": user.role, "location": user.location})
    return session


@router.post("/refresh")
def refresh(body: RefreshIn):
    session, _ = _session(_supabase("refresh_token", {"refresh_token": body.refresh_token}))
    return session


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return _profile(user)
