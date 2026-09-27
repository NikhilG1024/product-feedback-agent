import json
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app


def payload(now):
    stamp = now.isoformat()
    return {
        "schema_version": 1, "run_id": "run-1", "started_at": stamp,
        "updated_at": stamp, "status": "running", "total": 2,
        "workers": 1, "completed": 1, "failed": 0, "active": 1,
        "queued": 0, "published": 0,
        "products": {
            "B1": {"title": "One", "status": "citation_checks_passed"},
            "B2": {"title": "Two", "status": "generating", "error": "/secret/path"},
        },
    }


def get(client, suffix=""):
    return client.get("/api/v1/summary-initialization/progress" + suffix,
                      headers={"Authorization": "Bearer pm-secret-value"})


def test_pm_progress_is_bounded_and_does_not_claim_publication(tmp_path, settings):
    now = datetime.now(timezone.utc)
    path = tmp_path / "progress.json"
    path.write_text(json.dumps(payload(now)))
    client = TestClient(create_app(replace(settings, summary_initialization_progress_path=str(path))))
    response = get(client, "?limit=1")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "available"
    assert body["progress"]["completed"] == 1
    assert body["progress"]["published"] is None
    assert len(body["progress"]["products"]) == 1
    assert body["progress"]["next_offset"] == 1
    assert "/secret/path" not in response.text
    assert get(client, "?offset=1&limit=1").json()["progress"]["products"][0]["id"] == "B2"


def test_missing_invalid_and_stale_telemetry_are_explicit(tmp_path, settings):
    path = tmp_path / "progress.json"
    client = TestClient(create_app(replace(settings, summary_initialization_progress_path=str(path),
                                           summary_initialization_stale_seconds=120)))
    assert get(client).json()["availability"] == "unavailable"
    bad = payload(datetime.now(timezone.utc))
    bad["completed"] = 2
    path.write_text(json.dumps(bad))
    assert get(client).json()["availability"] == "unavailable"
    old = payload(datetime.fromtimestamp(1, timezone.utc))
    path.write_text(json.dumps(old))
    result = get(client).json()
    assert result["availability"] == "stale"
    assert result["progress"]["status"] == "running"


def test_progress_requires_pm_and_rejects_client_path(tmp_path, settings):
    path = tmp_path / "progress.json"
    path.write_text(json.dumps(payload(datetime.now(timezone.utc))))
    client = TestClient(create_app(replace(settings, summary_initialization_progress_path=str(path))))
    response = client.get("/api/v1/summary-initialization/progress",
                          headers={"Authorization": "Bearer reviewer-secret-value"})
    assert response.status_code == 403
    assert get(client, "?path=/secret").status_code == 422


def test_deeply_nested_json_is_unavailable_instead_of_server_error(tmp_path, settings):
    path = tmp_path / "progress.json"
    # Python 3.14's JSON decoder tolerates far more than 1,000 levels.
    path.write_text("[" * 200_000 + "0" + "]" * 200_000)
    client = TestClient(create_app(replace(settings, summary_initialization_progress_path=str(path))),
                        raise_server_exceptions=False)
    response = get(client)
    assert response.status_code == 200
    assert response.json() == {"availability": "unavailable", "progress": None}
