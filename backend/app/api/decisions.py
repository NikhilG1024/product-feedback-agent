"""PM decision persistence is independent of provider availability."""
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from app.api.auth import require_principal
from app.api.reviews import ProcessingResponse
from app.domain import DecisionInput, Principal
from app.errors import ServiceError

router = APIRouter(prefix='/api/v1')

class DecisionResponse(BaseModel):
    id: str
    parent_asin: str
    kind: str
    rationale: str
    evidence_ids: list[str]
    decided_at: datetime
    available_through: datetime
    processing: ProcessingResponse

class DecisionPage(BaseModel):
    items: list[DecisionResponse]


def service(request: Request):
    result = getattr(request.app.state.services, 'decisions', None)
    if result is None: raise ServiceError('service_unavailable', 503)
    return result

@router.post('/products/{product_id}/decisions', status_code=201, response_model=DecisionResponse)
def create(product_id: str, payload: DecisionInput, principal: Principal=Depends(require_principal), decisions=Depends(service)):
    return decisions.create(product_id, principal, payload)

@router.get('/products/{product_id}/decisions', response_model=DecisionPage)
def list_decisions(product_id: str, limit: int=Query(20, ge=1, le=100), principal: Principal=Depends(require_principal), decisions=Depends(service)):
    return decisions.list(product_id, principal, limit)
