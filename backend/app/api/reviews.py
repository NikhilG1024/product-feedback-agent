"""Authenticated reviewer routes with explicit public response contracts."""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from app.api.auth import require_principal
from app.domain import Principal, ReviewInput
from app.errors import ServiceError

router=APIRouter(prefix='/api/v1')

class ProcessingResponse(BaseModel):
    status: str
    attempts: int = 0
    memory_status: str | None = None
    classification_status: str | None = None
    error_code: str | None = None

class SummaryStatusResponse(BaseModel):
    status: Literal['saved','needs_initial_summary','waiting','queued','updating','included','failed']
    version: int | None = None

class SubmissionResponse(BaseModel):
    id: str
    processing: ProcessingResponse | None
    summary: SummaryStatusResponse | None = None

class QuoteResponse(BaseModel):
    review_id: str
    quote: str

class ProvisionalFinding(BaseModel):
    issue_type: str
    theme: str
    description: str
    evidence: list[QuoteResponse]
    review_ids: list[str]
    supporting_review_count: int
    provenance_validated: bool
    semantic_support: str
    evidence_sampled: bool

class ReviewResponse(BaseModel):
    id: str
    parent_asin: str
    asin: str
    title: str
    text: str
    rating: float
    timestamp: datetime
    source: str
    batch_id: str | None
    processing: ProcessingResponse | None
    provisional_findings: list[ProvisionalFinding] | None = None
    guidance_references: list[str] | None = None

class ReviewPage(BaseModel):
    items: list[ReviewResponse]
    next_cursor: str | None

class ReviewBatchResponse(BaseModel):
    id: str
    label: str
    review_count: int

class ReviewBatchList(BaseModel):
    items: list[ReviewBatchResponse]


def service(request: Request):
    result=getattr(request.app.state.services,'reviews',None)
    if result is None: raise ServiceError('service_unavailable',503)
    return result

@router.post('/products/{product_id}/reviews',status_code=201,response_model=SubmissionResponse)
def submit(product_id: str, payload: ReviewInput, key: str=Header(alias='Idempotency-Key',min_length=1,max_length=200), principal: Principal=Depends(require_principal), reviews=Depends(service)):
    return reviews.submit(product_id,principal,key,payload)

@router.get('/reviews/{review_id}/status',response_model=SubmissionResponse)
def status(review_id: str, principal: Principal=Depends(require_principal), reviews=Depends(service)):
    return reviews.status(review_id,principal)

@router.get('/products/{product_id}/reviews',response_model=ReviewPage)
def list_reviews(product_id: str, source: str | None=None, batch_id: str | None=None, cursor: str | None=None,
                 limit: int=Query(20,ge=1,le=100), sentiment: str | None=None,
                 rating: int | None=Query(None,ge=1,le=5), sort: str | None=None,
                 principal: Principal=Depends(require_principal), reviews=Depends(service)):
    return reviews.list(product_id,principal,source,batch_id,cursor,limit,
                        sentiment=sentiment,rating=rating,sort=sort)

@router.get('/products/{product_id}/review-batches',response_model=ReviewBatchList)
def review_batches(product_id: str, principal: Principal=Depends(require_principal), reviews=Depends(service)):
    return reviews.review_batches(product_id,principal)
