"""Public demo sessions are opt-in, scoped, and keep review ownership."""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4
import os

from fastapi.testclient import TestClient
from pymongo import MongoClient
import pytest

from app.api.auth import _guest_identity, authenticate_request, issue_guest_session
from app.errors import ServiceError
from app.main import create_app
from app.services.reviews import ReviewService
from app.summaries.contracts import SummaryView


class CachedSummary:
    def get(self, product_id, principal):
        assert principal.role == "pm"
        return SummaryView(product_id=product_id, current=None, last_updated_at=None,
            update_threshold=1, pending_review_count=0, status="uninitialized",
            error_code=None, memory_status="unknown")


@pytest.fixture
def database():
    from app.migrations.v2 import migrate
    client = MongoClient(os.environ["TEST_MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=2000)
    db = client["test_public_demo_" + uuid4().hex]
    migrate(db, False)
    db.products.insert_one({"_id": "P", "title": "Test product", "provenance": {}})
    yield db
    client.drop_database(db.name)
    client.close()


def _client(settings, database):
    return TestClient(create_app(settings, SimpleNamespace(
        reviews=ReviewService(database), summaries=CachedSummary())))


def test_public_demo_disabled_by_default(settings, database):
    client = _client(settings, database)
    response = client.post("/api/v1/demo/session")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "public_demo_disabled"
    assert client.get("/api/v1/products", headers={"Authorization": "Bearer pm-secret-value"}).status_code == 200


def test_guest_can_read_products_and_submit_but_only_read_own_review(settings, database):
    enabled = replace(settings, public_demo_enabled=True)
    client = _client(enabled, database)
    session = client.post("/api/v1/demo/session")
    assert session.status_code == 200
    assert session.headers["cache-control"] == "no-store"
    token = session.json()["token"]
    assert datetime.fromisoformat(session.json()["expires_at"]).tzinfo is not None
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/products", headers=headers).status_code == 200
    assert client.get("/api/v1/products/P", headers=headers).status_code == 200
    assert client.get("/api/v1/products/P/reviews", headers=headers).status_code == 200
    assert client.get("/api/v1/products/P/summary", headers=headers).status_code == 200
    submitted = client.post("/api/v1/products/P/reviews",
        headers={**headers, "Idempotency-Key": "one"},
        json={"title": "Review", "text": "The hinge broke", "rating": 2})
    assert submitted.status_code == 201
    review_id = submitted.json()["id"]
    assert client.get(f"/api/v1/reviews/{review_id}/status", headers=headers).status_code == 200
    other_token = client.post("/api/v1/demo/session").json()["token"]
    other = {"Authorization": "Bearer " + other_token}
    assert client.get(f"/api/v1/reviews/{review_id}/status", headers=other).status_code == 403
    assert client.get(f"/api/v1/reviews/{review_id}/events", headers=other).status_code == 403
    assert database.reviews.find_one({"_id": review_id})["author_id"].startswith("demo-guest:")


def test_guest_is_denied_admin_and_legacy_routes(settings, database):
    enabled = replace(settings, public_demo_enabled=True)
    client = _client(enabled, database)
    token = client.post("/api/v1/demo/session").json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.patch("/api/v1/products/P/summary/settings", headers=headers,
        json={"update_threshold": 2}).status_code == 403
    assert client.post("/api/v1/products/P/summary/refresh", headers=headers,
        json={"reason": "pending_reviews"}).status_code == 403
    assert client.post("/api/v1/products/P/summary/questions", headers=headers,
        json={"version": 1, "question": "Anything?"}).status_code == 403
    assert client.post("/api/v1/products/P/analysis-runs", headers=headers,
        json={"mode": "baseline"}).status_code == 403
    assert client.get("/api/v1/summary-initialization/progress", headers=headers).status_code == 403
    assert client.get("/api/v1/analysis-runs/x", headers=headers).status_code == 403


def test_guest_signature_expiry_and_method_scope(settings):
    enabled = replace(settings, public_demo_enabled=True)
    issued = issue_guest_session(enabled, now=1000)
    token = issued["token"]
    assert _guest_identity(token, enabled, now=1001).startswith("demo-guest:")
    with pytest.raises(ServiceError) as expired:
        _guest_identity(token, enabled, now=1000 + 8 * 60 * 60)
    assert expired.value.status_code == 401
    with pytest.raises(ServiceError) as tampered:
        _guest_identity(token[:-1] + ("A" if token[-1] != "A" else "B"), enabled, now=1001)
    assert tampered.value.status_code == 401
    with pytest.raises(ServiceError) as forbidden:
        authenticate_request(token, enabled, "DELETE", "/api/v1/products/P")
    assert forbidden.value.status_code in (401, 403)
