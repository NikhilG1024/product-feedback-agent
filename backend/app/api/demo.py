"""Opt-in, unauthenticated issue endpoint for short-lived public demo sessions."""
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.auth import issue_guest_session


router = APIRouter(prefix="/api/v1/demo")


class DemoSessionResponse(BaseModel):
    token: str
    expires_at: datetime


@router.post("/session", response_model=DemoSessionResponse)
def session(request: Request):
    result = issue_guest_session(request.app.state.settings)
    return JSONResponse(status_code=200,
        content=DemoSessionResponse.model_validate(result).model_dump(mode="json"),
        headers={"Cache-Control": "no-store"})
