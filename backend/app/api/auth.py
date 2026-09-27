"""Server-owned bearer identities and opt-in, route-scoped guest sessions."""

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import time
from uuid import UUID, uuid4

from fastapi import Depends, Request

from app.config import Settings
from app.domain import Principal
from app.errors import ServiceError


_GUEST_PREFIX = "gd1"
_GUEST_SECONDS = 8 * 60 * 60
_GUEST_KEY_CONTEXT = b"product-feedback-public-demo-session-v1"
_SEGMENT = r"[^/]+"
_GUEST_READS = (
    re.compile(r"/api/v1/products"),
    re.compile(rf"/api/v1/products/{_SEGMENT}"),
    re.compile(rf"/api/v1/products/{_SEGMENT}/review-batches"),
    re.compile(rf"/api/v1/products/{_SEGMENT}/reviews"),
    re.compile(rf"/api/v1/products/{_SEGMENT}/summary"),
    re.compile(rf"/api/v1/products/{_SEGMENT}/summary/history"),
    re.compile(rf"/api/v1/products/{_SEGMENT}/summary/versions/[0-9]+"),
    re.compile(rf"/api/v1/products/{_SEGMENT}/events"),
)
_GUEST_REVIEW_WRITE = re.compile(rf"/api/v1/products/{_SEGMENT}/reviews")
_GUEST_OWN_READS = (
    re.compile(rf"/api/v1/reviews/{_SEGMENT}/status"),
    re.compile(rf"/api/v1/reviews/{_SEGMENT}/events"),
)


def _key(settings: Settings) -> bytes:
    return hmac.new(settings.pm_token.encode("utf-8"), _GUEST_KEY_CONTEXT, hashlib.sha256).digest()


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("bad token")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _encode(decoded) != value:
        raise ValueError("noncanonical token")
    return decoded


def issue_guest_session(settings: Settings, *, now: int | None = None) -> dict:
    if not settings.public_demo_enabled:
        raise ServiceError("public_demo_disabled", 403)
    issued = int(time.time()) if now is None else now
    payload = {"v": 1, "sub": str(uuid4()), "iat": issued, "exp": issued + _GUEST_SECONDS}
    body = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signed = f"{_GUEST_PREFIX}.{body}"
    signature = _encode(hmac.new(_key(settings), signed.encode("ascii"), hashlib.sha256).digest())
    return {"token": f"{signed}.{signature}",
            "expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc)}


def _guest_identity(token: str, settings: Settings, *, now: int | None = None) -> str:
    try:
        if len(token) > 2048:
            raise ValueError("long token")
        prefix, body, signature = token.split(".")
        if prefix != _GUEST_PREFIX:
            raise ValueError("wrong prefix")
        expected = hmac.new(_key(settings), f"{prefix}.{body}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_decode(signature), expected):
            raise ValueError("wrong signature")
        payload = json.loads(_decode(body))
        if set(payload) != {"v", "sub", "iat", "exp"} or payload["v"] != 1:
            raise ValueError("wrong payload")
        issued, expires = payload["iat"], payload["exp"]
        current = int(time.time()) if now is None else now
        if (type(issued) is not int or type(expires) is not int or
                issued > current + 30 or expires <= current or
                expires - issued != _GUEST_SECONDS):
            raise ValueError("expired")
        subject = UUID(payload["sub"])
        if subject.version != 4 or str(subject) != payload["sub"]:
            raise ValueError("wrong subject")
        return "demo-guest:" + str(subject)
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error):
        raise ServiceError("unauthorized", 401) from None


def _guest_role(method: str, path: str) -> str:
    if method == "GET":
        if any(pattern.fullmatch(path) for pattern in _GUEST_READS):
            return "pm"
        if any(pattern.fullmatch(path) for pattern in _GUEST_OWN_READS):
            return "reviewer"
    if method == "POST" and _GUEST_REVIEW_WRITE.fullmatch(path):
        return "reviewer"
    raise ServiceError("forbidden", 403)


def authenticate(token: str, settings: Settings) -> Principal:
    reviewer_match = hmac.compare_digest(token, settings.reviewer_token)
    pm_match = hmac.compare_digest(token, settings.pm_token)
    if reviewer_match:
        return Principal(user_id=settings.reviewer_user_id, role="reviewer")
    if pm_match:
        return Principal(user_id=settings.pm_user_id, role="pm")
    raise ServiceError("unauthorized", 401)


def authenticate_request(token: str, settings: Settings, method: str, path: str) -> Principal:
    if token.startswith(_GUEST_PREFIX + "."):
        if not settings.public_demo_enabled:
            raise ServiceError("unauthorized", 401)
        identity = _guest_identity(token, settings)
        return Principal(user_id=identity, role=_guest_role(method, path))
    return authenticate(token, settings)


def require_principal(request: Request) -> Principal:
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or " " in token:
        raise ServiceError("unauthorized", 401)
    return authenticate_request(token, request.app.state.settings,
                                request.method, request.url.path)


def require_pm(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "pm":
        raise ServiceError("forbidden", 403)
    return principal
