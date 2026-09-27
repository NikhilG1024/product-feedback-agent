"""PM questions use one explicit completed analysis run."""
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, StringConstraints
from app.api.auth import require_pm
from app.domain import Principal, Evidence
from app.errors import ServiceError

router = APIRouter(prefix='/api/v1')

class QuestionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    question: Annotated[str, StringConstraints(min_length=1, max_length=2000)]

class QuestionResponse(BaseModel):
    answer: str
    evidence: list[Evidence]
    insufficient_evidence: bool

def service(request: Request):
    questions = getattr(request.app.state.services, 'questions', None)
    if questions is None: raise ServiceError('service_unavailable', 503)
    return questions

@router.post('/products/{product_id}/questions', response_model=QuestionResponse)
def answer(product_id: str, payload: QuestionInput, principal: Principal = Depends(require_pm), questions=Depends(service)):
    return questions.answer(product_id, payload.run_id, payload.question)
