"""Optional identity on top of the anonymous session model.

A session has never required an account: it is an opaque id the browser
generates and the server tracks. Signing in with Clerk does not change that -
it only *attaches* a user id to a session that already works without one.
Every route that reads identity through :data:`CurrentUserDep` gets ``None``
for a request with no (or an invalid) ``Authorization`` header, and keeps
answering exactly as it did before this module existed.

Verification is signature-only, against Clerk's own published JWKS - this
process never calls Clerk's API and never sees a password. ``PyJWKClient``
fetches and caches Clerk's public keys itself; it does a **blocking** HTTP
request on a cache miss (rare - keys are cached for its default lifetime),
which is why :func:`current_user_id` is a plain ``def``: FastAPI runs a sync
dependency in a worker thread, so that occasional fetch never blocks the
event loop. Hand-rolling an async JWKS cache would avoid that thread hop, but
for a handful of key rotations a year it is not worth the extra code.
"""

from __future__ import annotations

import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: One JWKS client per issuer, built lazily. In practice there is exactly one
#: (this deployment's Clerk app), but keying by issuer costs nothing and means
#: a settings change in tests does not reuse another issuer's cached keys.
_jwk_clients: dict[str, PyJWKClient] = {}


def _client_for(issuer: str) -> PyJWKClient:
    client = _jwk_clients.get(issuer)
    if client is None:
        client = PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json")
        _jwk_clients[issuer] = client
    return client


def verify_token(token: str, settings: Settings) -> str | None:
    """The Clerk user id (``sub``) for a valid, signed, unexpired token.

    Returns ``None`` for anything else - no issuer configured, an unreachable
    JWKS endpoint, an expired or malformed token, a signature that does not
    match. This never raises: every caller treats identity as optional, so a
    verification failure is exactly as anonymous as no token at all, not an
    error to surface.
    """
    if not settings.auth_enabled:
        return None
    try:
        client = _client_for(settings.clerk_issuer)
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            # Clerk's session tokens are scoped by issuer, not by an audience
            # claim this backend would need to register ahead of time.
            options={"verify_aud": False},
        )
    except Exception:
        logger.debug("token verification failed", exc_info=True)
        return None
    subject = claims.get("sub")
    return subject if isinstance(subject, str) else None


def current_user_id(request: Request) -> str | None:
    """FastAPI dependency: the signed-in user's id, or ``None`` for anonymous.

    Reads ``Authorization: Bearer <token>``. Absent, malformed, or unverifiable
    all resolve to the same ``None`` - there is no "logged in but invalid"
    state a route needs to handle separately.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[len("bearer "):].strip()
    if not token:
        return None
    settings = getattr(request.app.state, "settings", None) or get_settings()
    return verify_token(token, settings)


CurrentUserDep = Annotated[str | None, Depends(current_user_id)]


def require_user_id(user_id: CurrentUserDep) -> str:
    """FastAPI dependency for the handful of routes that only make sense for
    a signed-in account (listing or claiming *your* sessions). Everything else
    in the app takes :data:`CurrentUserDep` instead and stays anonymous-first.
    """
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign-in required")
    return user_id


RequireUserDep = Annotated[str, Depends(require_user_id)]
