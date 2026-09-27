"""Authenticated PM summary routes."""

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.auth import require_pm
from app.domain import Principal
from app.errors import ServiceError
from app.integrations.llm import Answer
from app.summaries.contracts import (HistoryPage, RefreshInput, SummaryQuestionInput,
                                     SummarySettingsInput, SummaryVersion, SummaryView)

router = APIRouter(prefix="/api/v1/products/{product_id}/summary")


def service(request: Request):
    result = getattr(request.app.state.services, "summaries", None)
    if result is None:
        raise ServiceError("service_unavailable", 503)
    return result


@router.get("", response_model=SummaryView)
def get_summary(product_id: str, principal: Principal = Depends(require_pm), summaries=Depends(service)):
    return summaries.get(product_id, principal)


@router.get("/history", response_model=HistoryPage)
def history(product_id: str, cursor: str | None = None, limit: int = Query(20, ge=1, le=100),
            principal: Principal = Depends(require_pm), summaries=Depends(service)):
    return summaries.history(product_id, principal, cursor, limit)


@router.get("/versions/{version}", response_model=SummaryVersion)
def version(product_id: str, version: int, principal: Principal = Depends(require_pm), summaries=Depends(service)):
    return summaries.get_version(product_id, principal, version)


@router.patch("/settings", response_model=SummaryView)
def settings(product_id: str, payload: SummarySettingsInput, principal: Principal = Depends(require_pm), summaries=Depends(service)):
    return summaries.settings(product_id, principal, payload)


@router.post("/refresh", status_code=202, response_model=SummaryView)
def refresh(product_id: str, payload: RefreshInput,
            key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
            principal: Principal = Depends(require_pm), summaries=Depends(service)):
    return summaries.refresh(product_id, principal, key, payload)


@router.post("/questions", response_model=Answer)
def question(product_id: str, payload: SummaryQuestionInput,
             principal: Principal = Depends(require_pm), summaries=Depends(service)):
    return summaries.question(product_id, principal, payload)
