"""Bounded SSE generator and pre-stream authorization checks."""
import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.api.events import _events, _frame, _review_revision, review_events
from app.domain import Principal
from app.errors import ServiceError
from app.main import create_app


class RequestProbe:
    def __init__(self, disconnected=False):
        self.disconnected = disconnected

    async def is_disconnected(self):
        return self.disconnected


def test_initial_snapshot_then_only_changed_event_and_disconnect():
    async def check():
        request = RequestProbe()
        snapshots = iter([{"summary": {"version": 1}}, {"summary": {"version": 2}}])
        stream = _events(request, {"summary": {"version": 1}}, lambda: next(snapshots),
                         poll_seconds=0.001, heartbeat_seconds=60)
        assert await anext(stream) == 'event: summary\ndata: {"version":1}\n\n'
        assert await asyncio.wait_for(anext(stream), 1) == 'event: summary\ndata: {"version":2}\n\n'
        request.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
    asyncio.run(check())


def test_idle_stream_sends_comment_heartbeat():
    async def check():
        stream = _events(RequestProbe(), {"submission": {"id": "r1"}},
                         lambda: {"submission": {"id": "r1"}},
                         poll_seconds=0.001, heartbeat_seconds=0)
        assert (await anext(stream)).startswith("event: submission\n")
        assert await asyncio.wait_for(anext(stream), 1) == ": heartbeat\n\n"
        await stream.aclose()
    asyncio.run(check())


def test_stream_closes_at_max_age_before_another_poll():
    async def check():
        calls=[]
        stream=_events(RequestProbe(), {"summary":{"version":1}},
                       lambda: calls.append(1) or {"summary":{"version":2}},
                       poll_seconds=0.1,heartbeat_seconds=1,max_age_seconds=0.001)
        assert (await anext(stream)).startswith("event: summary\n")
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream),1)
        assert calls==[]
    asyncio.run(check())


def test_vercel_duration_exceeds_stream_age():
    import json
    from pathlib import Path
    from app.api.events import _MAX_AGE_SECONDS
    config=json.loads((Path(__file__).resolve().parents[2]/"vercel.json").read_text())
    assert _MAX_AGE_SECONDS<=240
    assert config["functions"]["index.py"]["maxDuration"]==300
    assert _MAX_AGE_SECONDS<config["functions"]["index.py"]["maxDuration"]


def test_frame_uses_one_json_data_line_and_no_leaked_newline():
    assert _frame("submission", {"text": "line 1\nline 2"}) == (
        'event: submission\ndata: {"text":"line 1\\nline 2"}\n\n')


def test_review_revision_is_stable_and_changes_with_latest_insert():
    class Collection:
        row = None

        def find_one(self, query, projection, sort):
            assert query == {"parent_asin": "P", "source": "user_submission"}
            assert sort == [("timestamp", -1), ("_id", -1)]
            return self.row

    collection = Collection()
    reviews = SimpleNamespace(repository=SimpleNamespace(
        database=SimpleNamespace(reviews=collection)))
    assert _review_revision(reviews, "P") == "none"
    from datetime import datetime, timezone
    collection.row = {"_id": "r1", "timestamp": datetime(2026, 9, 28, tzinfo=timezone.utc)}
    first = _review_revision(reviews, "P")
    assert _review_revision(reviews, "P") == first
    collection.row = {"_id": "r2", "timestamp": datetime(2026, 9, 28, tzinfo=timezone.utc)}
    assert _review_revision(reviews, "P") != first


def test_sse_routes_require_same_bearer_roles_as_existing_reads(settings):
    app = create_app(settings, SimpleNamespace(
        summaries=SimpleNamespace(get=lambda *_: None),
        reviews=SimpleNamespace(status=lambda *_: None),
        analysis=SimpleNamespace(get=lambda *_: None)))
    client = TestClient(app)
    assert client.get("/api/v1/products/P/events").status_code == 401
    assert client.get("/api/v1/reviews/r/events").status_code == 401
    assert client.get("/api/v1/analysis-runs/r/events").status_code == 401
    reviewer = {"Authorization": "Bearer reviewer-secret-value"}
    assert client.get("/api/v1/products/P/events", headers=reviewer).status_code == 403
    assert client.get("/api/v1/analysis-runs/r/events", headers=reviewer).status_code == 403


def test_review_event_preflight_enforces_status_ownership():
    def status(_review_id, principal):
        if principal.user_id != "owner":
            raise ServiceError("forbidden", 403)
        return {"id": "r1", "processing": {"status": "pending"},
                "summary": {"status": "saved", "version": None}}
    async def check():
        with pytest.raises(ServiceError) as exc:
            await review_events("r1", RequestProbe(), Principal(user_id="intruder", role="reviewer"),
                                SimpleNamespace(status=status))
        assert exc.value.status_code == 403
        response = await review_events("r1", RequestProbe(), Principal(user_id="owner", role="reviewer"),
                                       SimpleNamespace(status=status))
        assert response.media_type == "text/event-stream"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache, no-transform"
        first = await anext(response.body_iterator)
        assert first.startswith('event: submission\ndata: {"id":"r1"')
        await response.body_iterator.aclose()
    asyncio.run(check())
