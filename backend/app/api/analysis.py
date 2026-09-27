"""PM analysis endpoints expose only completed, validated report content."""
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from app.api.auth import require_pm
from app.domain import AnalysisInput, Principal, Scope, Evidence
from app.errors import ServiceError

router = APIRouter(prefix='/api/v1')


class SupportingReviewCount(BaseModel):
    finding_id: str
    theme: str
    supporting_review_count: int


class GuidanceReference(BaseModel):
    decision_id: str
    kind: str
    rationale: str


class AnalysisResponse(BaseModel):
    id: str
    parent_asin: str
    mode: str
    scope: Scope
    status: str
    created_at: datetime
    available_through: datetime
    snapshot_hash: str
    denominator: int
    stale: bool
    error_code: str | None = None
    summary: str | None
    supporting_review_counts: list[SupportingReviewCount] | None
    guidance_references: list[GuidanceReference] | None
    trend: None = None
    limitations: list[str] | None
    investigation_suggestions: list[str] | None


class FindingResponse(BaseModel):
    id: str
    issue_type: str
    theme: str
    description: str
    evidence: list[Evidence]
    review_ids: list[str]
    supporting_review_count: int
    provenance_validated: bool
    semantic_support: str
    evidence_sampled: bool


class FindingsResponse(BaseModel):
    run_id: str
    denominator: int
    items: list[FindingResponse]


def service(request: Request):
    analysis = getattr(request.app.state.services, 'analysis', None)
    if analysis is None: raise ServiceError('service_unavailable', 503)
    return analysis


@router.post('/products/{product_id}/analysis-runs', status_code=202, response_model=AnalysisResponse)
def enqueue(product_id: str, payload: AnalysisInput, principal: Principal = Depends(require_pm), analysis=Depends(service)):
    return analysis.enqueue(product_id, principal, payload)


@router.get('/analysis-runs/{run_id}', response_model=AnalysisResponse)
def get(run_id: str, principal: Principal = Depends(require_pm), analysis=Depends(service)):
    return analysis.get(run_id)


@router.get('/products/{product_id}/findings', response_model=FindingsResponse)
def findings(product_id: str, run_id: str = Query(min_length=1), principal: Principal = Depends(require_pm), analysis=Depends(service)):
    return analysis.findings(product_id, run_id)
