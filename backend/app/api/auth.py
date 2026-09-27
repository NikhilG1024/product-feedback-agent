"""Server-owned demo bearer identities."""

from hmac import compare_digest

from fastapi import Depends, Request

from app.config import Settings
from app.domain import Principal
from app.errors import ServiceError


def authenticate(token: str, settings: Settings) -> Principal:
    reviewer_match = compare_digest(token, settings.reviewer_token)
    pm_match = compare_digest(token, settings.pm_token)
    if reviewer_match:
        return Principal(user_id=settings.reviewer_user_id, role="reviewer")
    if pm_match:
        return Principal(user_id=settings.pm_user_id, role="pm")
    raise ServiceError("unauthorized", 401)


def require_principal(request: Request) -> Principal:
    scheme, separator, token = request.headers.get("authorization", "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not token or " " in token:
        raise ServiceError("unauthorized", 401)
    return authenticate(token, request.app.state.settings)


def require_pm(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "pm":
        raise ServiceError("forbidden", 403)
    return principal
