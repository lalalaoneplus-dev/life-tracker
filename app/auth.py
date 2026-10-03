"""Two principals: 'agent' (bearer, full write) and 'session' (signed cookie, read + toggle).

The agent bearer is compared constant-time and NEVER written to any response, HTML,
URL, or log. The session cookie is an HMAC of a fixed principal string under the
session secret — no secret material is placed in the cookie itself.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

COOKIE = "lt_session"
_PRINCIPAL = "browser"  # single-user; the cookie only proves local unlock


def sign_session(secret: str) -> str:
    mac = hmac.new(secret.encode(), _PRINCIPAL.encode(), hashlib.sha256).hexdigest()
    return f"{_PRINCIPAL}.{mac}"


def valid_session(token: str | None, secret: str) -> bool:
    if not token or "." not in token:
        return False
    principal, mac = token.rsplit(".", 1)
    if principal != _PRINCIPAL:
        return False
    expected = hmac.new(secret.encode(), principal.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, expected)


def principal(request: Request) -> str | None:
    """Return 'agent', 'session', or None. Bearer wins if present and correct."""
    settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        if hmac.compare_digest(token, settings.agent_token):
            return "agent"
        return None  # a wrong bearer is not downgraded to session
    if valid_session(request.cookies.get(COOKIE), settings.session_secret):
        return "session"
    return None


def require_reader(request: Request) -> str:
    p = principal(request)
    if p is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return p


def require_agent(request: Request) -> str:
    p = principal(request)
    if p is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if p != "agent":
        raise HTTPException(status_code=403, detail="agent bearer required for writes")
    return p
