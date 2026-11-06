"""JWT Bearer authentication with optional role/scope checks."""
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings, get_settings
from .errors import forbidden, unauthorized

_bearer = HTTPBearer(auto_error=False)
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def _decode(token: str, settings: Settings) -> dict[str, Any]:
    options = {"verify_aud": bool(settings.jwt_audience)}
    kwargs: dict[str, Any] = {
        "algorithms": settings.jwt_algorithms,
        "audience": settings.jwt_audience,
        "issuer": settings.jwt_issuer,
        "options": options,
    }
    if settings.jwt_jwks_url:
        client = _jwks_clients.setdefault(settings.jwt_jwks_url, jwt.PyJWKClient(settings.jwt_jwks_url))
        key = client.get_signing_key_from_jwt(token).key
    elif settings.jwt_secret:
        key = settings.jwt_secret
        kwargs["algorithms"] = [a for a in settings.jwt_algorithms if a.startswith("HS")] or ["HS256"]
    else:
        raise unauthorized()
    return jwt.decode(token, key, **kwargs)


def _claims_grant(claims: dict[str, Any], required: str) -> bool:
    roles = claims.get("roles") or []
    if isinstance(roles, str):
        roles = roles.split()
    scopes = claims.get("scp") or claims.get("scope") or ""
    scopes = scopes.split() if isinstance(scopes, str) else list(scopes)
    return required in roles or required in scopes


def require_role(setting_name: str):
    """Dependency factory: validates the Bearer JWT and the role named in settings."""

    async def dependency(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if not settings.auth_enabled:
            return {}
        if creds is None or creds.scheme.lower() != "bearer":
            raise unauthorized()
        try:
            claims = _decode(creds.credentials, settings)
        except (jwt.PyJWTError, ValueError):
            raise unauthorized()
        required = getattr(settings, setting_name)
        if required and not _claims_grant(claims, required):
            raise forbidden()
        request.state.claims = claims
        return claims

    return dependency


require_combine = require_role("combine_required_role")
require_mask = require_role("mask_required_role")
