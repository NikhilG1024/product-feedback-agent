"""PM-only initialization telemetry endpoint."""

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import require_pm
from app.summaries.progress import read_progress


router = APIRouter(prefix="/api/v1/summary-initialization", dependencies=[Depends(require_pm)])


@router.get("/progress")
def progress(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)) -> dict:
    # No arbitrary query parameters: callers cannot influence the local source file.
    if set(request.query_params) - {"offset", "limit"}:
        from fastapi import HTTPException
        raise HTTPException(status_code=422)
    settings = request.app.state.settings
    result = read_progress(settings.summary_initialization_progress_path,
                           settings.summary_initialization_stale_seconds)
    snapshot = result["progress"]
    if snapshot is not None:
        items = snapshot["products"]
        snapshot["products"] = items[offset:offset + limit]
        snapshot["next_offset"] = offset + limit if offset + limit < len(items) else None
    return result
