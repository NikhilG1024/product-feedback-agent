"""Public summary routes preserve PM isolation and read-only GET semantics."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.summaries.contracts import HistoryPage, SummaryView


class FakeSummaries:
    def __init__(self):
        self.calls = []

    def get(self, product, principal):
        self.calls.append(("get", product, principal.role))
        return SummaryView(product_id=product, current=None, last_updated_at=None,
            update_threshold=1, pending_review_count=0, status="uninitialized",
            error_code=None, memory_status="unknown")

    def history(self, product, principal, cursor, limit):
        self.calls.append(("history", product, cursor, limit))
        return HistoryPage(items=[], next_cursor=None)

    def get_version(self, product, principal, version):
        self.calls.append(("version", product, version))
        return None

    def settings(self, product, principal, payload):
        self.calls.append(("settings", product, payload.update_threshold))
        return self.get(product, principal).model_copy(update={"update_threshold": payload.update_threshold})

    def refresh(self, product, principal, key, payload):
        self.calls.append(("refresh", product, key, payload.reason))
        return self.get(product, principal)

    def question(self, product, principal, payload):
        self.calls.append(("question", product, payload.version, payload.question))
        return {"answer": "Insufficient evidence", "evidence": [], "insufficient_evidence": True}


PM = {"Authorization": "Bearer pm-secret-value"}
REVIEWER = {"Authorization": "Bearer reviewer-secret-value"}


def test_initial_summary_is_explicit_and_get_does_not_enqueue(settings):
    summaries = FakeSummaries()
    client = TestClient(create_app(settings, SimpleNamespace(summaries=summaries)))
    response = client.get("/api/v1/products/P/summary", headers=PM)
    assert response.status_code == 200
    assert response.json()["current"] is None
    assert response.json()["status"] == "uninitialized"
    assert summaries.calls == [("get", "P", "pm")]
    assert client.get("/api/v1/products/P/summary", headers=REVIEWER).status_code == 403
    assert summaries.calls == [("get", "P", "pm")]


def test_history_settings_refresh_and_question_contracts(settings):
    summaries = FakeSummaries()
    client = TestClient(create_app(settings, SimpleNamespace(summaries=summaries)))
    assert client.get("/api/v1/products/P/summary/history?limit=2&cursor=3", headers=PM).status_code == 200
    assert ("history", "P", "3", 2) in summaries.calls
    assert client.patch("/api/v1/products/P/summary/settings", headers=PM,
                        json={"update_threshold": True}).status_code == 422
    assert client.patch("/api/v1/products/P/summary/settings", headers=PM,
                        json={"update_threshold": 101}).status_code == 422
    assert client.patch("/api/v1/products/P/summary/settings", headers=PM,
                        json={"update_threshold": 5}).json()["update_threshold"] == 5
    assert client.post("/api/v1/products/P/summary/refresh", headers=PM,
                       json={"reason": "pending_reviews"}).status_code == 422
    assert client.post("/api/v1/products/P/summary/refresh", headers={**PM, "Idempotency-Key": "key"},
                       json={"reason": "pending_reviews"}).status_code == 202
    assert client.post("/api/v1/products/P/summary/questions", headers=PM,
                       json={"version": 1, "question": "What changed?"}).json()["insufficient_evidence"] is True
    assert client.post("/api/v1/products/P/summary/questions", headers=REVIEWER,
                       json={"version": 1, "question": "What changed?"}).status_code == 403
