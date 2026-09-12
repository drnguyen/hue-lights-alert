"""Admin session cookie (predefined ADMIN_TOKEN) and API bearer-token authentication."""

from __future__ import annotations

import secrets
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "hue_admin_session"


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.session_secret, salt="admin-session")


def check_admin_token(request: Request, candidate: str) -> bool:
    expected = request.app.state.settings.admin_token
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def issue_session(request: Request) -> str:
    return _serializer(request).dumps({"admin": True, "nonce": secrets.token_hex(8)})


def session_max_age(request: Request) -> int:
    return request.app.state.settings.session_ttl_hours * 3600


def current_admin(request: Request) -> Optional[dict[str, Any]]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        data = _serializer(request).loads(cookie, max_age=session_max_age(request))
    except (BadSignature, SignatureExpired):
        return None
    return data if data.get("admin") else None


def require_admin(request: Request) -> dict[str, Any]:
    session = current_admin(request)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required")
    return session


def require_api_token(request: Request) -> dict[str, Any]:
    """Accept `Authorization: Bearer <token>` or `X-API-Key: <token>`."""
    header = request.headers.get("authorization", "")
    token = ""
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        token = request.headers.get("x-api-key", "").strip()
    record = request.app.state.store.verify_token(token) if token else None
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record
