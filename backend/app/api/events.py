"""Authenticated, snapshot-based server-sent events for live dashboard state.

The server checks Mongo-backed service snapshots every two seconds. Each connection
starts with a full snapshot, so clients need no event log or last-event cursor.
"""
import asyncio
from collections.abc import Callable
import json
from time import monotonic

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.analysis import AnalysisResponse, service as analysis_service
from app.api.auth import require_pm, require_principal
from app.api.reviews import SubmissionResponse, service as reviews_service
from app.domain import Principal
from app.summaries.api import service as summaries_service
from app.summaries.contracts import SummaryView


router = APIRouter(prefix="/api/v1")
_POLL_SECONDS = 2.0
_HEARTBEAT_SECONDS = 15.0
_MAX_AGE_SECONDS = 240.0
_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def _frame(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _encoded(model: type[BaseModel], value) -> dict:
    return model.model_validate(value).model_dump(mode="json")


async def _events(request: Request, initial: dict[str, dict], read: Callable[[], dict[str, dict]],
                  *, poll_seconds: float = _POLL_SECONDS,
                  heartbeat_seconds: float = _HEARTBEAT_SECONDS,
                  max_age_seconds: float = _MAX_AGE_SECONDS):
    """Yield snapshots and heartbeats, then close before the function deadline."""
    deadline = monotonic() + max_age_seconds
    previous = initial
    for event, payload in initial.items():
        yield _frame(event, payload)
    last_write = monotonic()
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(poll_seconds, remaining))
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        if await request.is_disconnected():
            return
        try:
            snapshot = await asyncio.wait_for(asyncio.to_thread(read), timeout=remaining)
        except TimeoutError:
            return
        if monotonic() >= deadline:
            return
        changed = False
        for event, payload in snapshot.items():
            if payload != previous.get(event):
                yield _frame(event, payload)
                changed = True
        previous = snapshot
        if changed:
            last_write = monotonic()
        elif monotonic() - last_write >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_write = monotonic()


def _response(request: Request, initial: dict[str, dict], read: Callable[[], dict[str, dict]]):
    return StreamingResponse(_events(request, initial, read), media_type="text/event-stream",
                             headers=_HEADERS)


def _review_revision(reviews, product_id: str) -> str:
    row = reviews.repository.database.reviews.find_one(
        {"parent_asin": product_id, "source": "user_submission"},
        {"_id": 1, "timestamp": 1}, sort=[("timestamp", -1), ("_id", -1)])
    if row is None:
        return "none"
    stamp = row.get("timestamp")
    return f"{stamp.isoformat() if stamp is not None else ''}:{row['_id']}"


@router.get("/products/{product_id}/events")
async def product_events(product_id: str, request: Request,
                         principal: Principal = Depends(require_pm),
                         summaries=Depends(summaries_service),
                         reviews=Depends(reviews_service)):
    def read():
        return {"summary": _encoded(SummaryView, summaries.get(product_id, principal)),
                "reviews_changed": {"revision": _review_revision(reviews, product_id)}}
    initial = await asyncio.to_thread(read)  # Auth and product existence fail before stream headers.
    return _response(request, initial, read)


@router.get("/reviews/{review_id}/events")
async def review_events(review_id: str, request: Request,
                        principal: Principal = Depends(require_principal),
                        reviews=Depends(reviews_service)):
    def read():
        return {"submission": _encoded(SubmissionResponse, reviews.status(review_id, principal))}
    initial = await asyncio.to_thread(read)  # Same ownership check as status GET.
    return _response(request, initial, read)


@router.get("/analysis-runs/{run_id}/events")
async def analysis_events(run_id: str, request: Request,
                          principal: Principal = Depends(require_pm),
                          analysis=Depends(analysis_service)):
    def read():
        return {"analysis": _encoded(AnalysisResponse, analysis.get(run_id))}
    initial = await asyncio.to_thread(read)
    return _response(request, initial, read)
