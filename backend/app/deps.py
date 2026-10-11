"""Auth dependencies. JWT claims used: sub, role, location (role/location may
sit at the top level or under app_metadata, as Supabase issues them)."""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import get_settings

_bearer = HTTPBearer(auto_error=False)

# Roles: standard = own location only, read-only · viewer = every location, read-only ·
# support = every location + uploads (who may upload is configured by UPLOAD_ROLES).
ROLES = ("standard", "viewer", "support")
ALL_LOCATION_ROLES = ("viewer", "support")


class CurrentUser(BaseModel):
    id: str
    email: str | None = None
    role: str = "standard"
    location: str | None = None

    @property
    def is_support(self) -> bool:
        return self.role == "support"

    @property
    def sees_all_locations(self) -> bool:
        """support (all data + uploads) and viewer (all data, read-only) are not tied to one location."""
        return self.role in ALL_LOCATION_ROLES

    @property
    def actor(self) -> str:
        return self.email or self.id


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{get_settings().supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json")


def _decode(token: str) -> dict:
    s = get_settings()
    if s.jwt_secret:
        return jwt.decode(token, s.jwt_secret, algorithms=["HS256"], audience=s.jwt_audience)
    if s.supabase_url:
        key = _jwks().get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["ES256", "RS256"], audience=s.jwt_audience)
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Auth is not configured (JWT_SECRET / SUPABASE_URL)")


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> CurrentUser:
    s = get_settings()
    if creds is None:
        if s.dev_auth_bypass:   # stub until the auth module is merged; OFF by default
            return CurrentUser(id="dev-user", email="dev@local", role="support")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = _decode(creds.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}",
                            headers={"WWW-Authenticate": "Bearer"}) from e
    meta = claims.get("app_metadata") or {}
    return CurrentUser(id=claims["sub"], email=claims.get("email"),
                       role=meta.get("role") or claims.get("role") or "standard",
                       location=meta.get("location") or claims.get("location"))


def require_upload_role(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in get_settings().upload_roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user.role}' may not upload data")
    return user
