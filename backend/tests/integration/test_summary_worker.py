"""Incremental worker and review admission on a disposable Mongo database."""

from datetime import datetime, timezone
import json
import os
from uuid import uuid4
from types import SimpleNamespace

import pytest
from pymongo import MongoClient
from fastapi.testclient import TestClient

from app.domain import Principal, ReviewInput
from app.config import Settings
from app.main import create_app
from app.migrations.v2 import migrate as migrate_v2
from app.migrations.v3 import migrate as migrate_v3
from app.services.reviews import ReviewService
from app.summaries.contracts import SummaryCoverage, SummaryVersion
from app.summaries.repository import SummaryRepository, artifact_sha256
from app.summaries.service import SummaryService
from app.summaries.worker import SummaryWorker


@pytest.fixture
def setup():
    client = MongoClient(os.environ["TEST_MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=2000)
    database = client["test_summary_worker_" + uuid4().hex]
    migrate_v2(database, False)
    migrate_v3(database, False)
    database.products.insert_one({"_id": "P", "title": "Product", "provenance": {}})
    repo = SummaryRepository(database)
    yield database, repo
    client.drop_database(database.name)
    client.close()


def publish_empty_initial(repo):
    now = datetime.now(timezone.utc)
    version = SummaryVersion(product_id="P", version=1, parent_version=None,
        job_id="fixture-initial", kind="initial", narrative="Initial summary", themes=[],
        coverage=SummaryCoverage(historical_sample_count=0, new_review_count=0),
        delta_review_ids=[], manifest_ref="empty-sample", model_identity="test-model",
        prompt_version="test", guidance_references=[], created_at=now, published_at=now)
    version.semantic_review.status = "approved"
    version.semantic_review.reviewer_id = "test-reviewer"
    version.semantic_review.reviewer_type = "human"
    version.semantic_review.reviewed_at = now
    version.semantic_review.rubric_version = "v1"
    version.semantic_review.factual_support = True
    version.semantic_review.coverage = True
    version.semantic_review.classification = True
    version.semantic_review.artifact_sha256 = artifact_sha256(version)
    document = version.model_dump(mode="python")
    document["_id"] = "P:1"
    repo.versions.insert_one(document)
    repo._state("P")
    repo.states.update_one({"_id": "P"}, {"$set": {"current_version": 1, "next_version": 2,
                        "last_updated_at": now, "status": "ready"}})


class Provider:
    model = "test-model"
    def __init__(self):
        self.calls = 0

    def generate_summary(self, messages, output_schema):
        self.calls += 1
        data = json.loads(messages[1]["content"])
        previous = data["previous"] or {"themes": [], "contradictions": []}
        themes = list(previous["themes"])
        for review in data["new_reviews"]:
            themes.append({"id": review["id"], "description": "Reported experience",
                           "issue_type": "other", "polarity": "neutral",
                           "evidence": [{"review_id": review["id"], "quote": review["text"]}]})
        return {"narrative": "Summary includes new feedback", "themes": themes,
                "contradictions": previous.get("contradictions", [])}


class Memory:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def ensure_retained(self, bank_id, record, *, state=None, checkpoint=None):
        from app.integrations.memory import ProviderError
        self.calls.append((bank_id, record.id))
        checkpoint({"record_id": record.id, "status": "pending"})
        if self.fail:
            raise ProviderError("memory_unavailable")


def test_review_acknowledgement_threshold_and_inclusion(setup):
    database, repo = setup
    publish_empty_initial(repo)
    repo.set_threshold("P", 2)
    reviews = ReviewService(database, summaries=repo)
    principal = Principal(user_id="reviewer", role="reviewer")
    first = reviews.submit("P", principal, "one", ReviewInput(title="First", text="The hinge broke", rating=2))
    assert first["summary"]["status"] == "waiting"
    provider = Provider()
    memory = Memory()
    worker = SummaryWorker(database, repo, provider, memory=memory)
    assert worker.sync_memory_once() is True
    assert worker.tick() is False
    second = reviews.submit("P", principal, "two", ReviewInput(title="Second", text="The button sticks", rating=2))
    assert reviews.submit("P", principal, "two", ReviewInput(title="Second", text="The button sticks", rating=2))["id"] == second["id"]
    assert worker.tick() is True
    assert provider.calls == 1
    assert repo.current("P").current.coverage.new_review_count == 2
    assert reviews.status(first["id"], principal)["summary"] == {"status": "included", "version": 2}
    assert reviews.status(second["id"], principal)["summary"] == {"status": "included", "version": 2}
    assert repo.current("P").memory_status == "pending"
    assert worker.sync_memory_once() is True
    assert repo.current("P").memory_status == "synced"
    assert ("summary-P", "summary:P:v2") in memory.calls


def test_crash_marker_reconciles_without_importing_history(setup):
    database, repo = setup
    publish_empty_initial(repo)
    review = {"_id": "missed", "source": "user_submission", "parent_asin": "P",
              "asin": "P", "title": "Missed", "text": "It broke", "rating": 2,
              "timestamp": datetime.now(timezone.utc), "timestamp_ms": 1,
              "created_at": datetime.now(timezone.utc), "author_id": "reviewer", "version": 1,
              "provenance": {}, "processing": {"status": "pending"},
              "idempotency_key": "missed", "payload_digest": "digest",
              "summary_input_outstanding": True}
    database.reviews.insert_one(review)
    database.reviews.insert_one({**review, "_id": "imported", "source": "amazon_2023",
                                 "batch": "A", "batch_id": "A", "held_out": False, "dataset_id": "D"})
    worker = SummaryWorker(database, repo, Provider(), memory=Memory())
    assert worker.reconcile_submissions() == 1
    assert repo.current("P").pending_review_count == 1
    assert database.reviews.find_one({"_id": "missed"})["summary_input_outstanding"] is False
    assert database.product_summary_inputs.count_documents({"review_id": "imported"}) == 0


def test_hindsight_failure_does_not_undo_published_summary(setup):
    database, repo = setup
    publish_empty_initial(repo)
    memory = Memory()
    worker = SummaryWorker(database, repo, Provider(), memory=memory)
    repo.current("P")
    assert worker.sync_memory_once() is True
    principal = Principal(user_id="reviewer", role="reviewer")
    ReviewService(database, summaries=repo).submit("P", principal, "one",
        ReviewInput(title="Review", text="The hinge broke", rating=2))
    assert worker.tick() is True
    assert repo.current("P").current.version == 2
    memory.fail = True
    assert worker.sync_memory_once() is True
    assert repo.current("P").current.version == 2
    assert repo.current("P").memory_status == "failed"
    database.product_summary_versions.update_one({"product_id": "P", "version": 2},
        {"$set": {"memory_sync.next_attempt_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}})
    memory.fail = False
    assert worker.sync_memory_once() is True
    assert repo.current("P").memory_status == "synced"
    assert [record_id for _, record_id in memory.calls if record_id == "summary:P:v2"] == [
        "summary:P:v2", "summary:P:v2"]


def test_summary_routes_replay_isolation_and_historical_questions(setup):
    database, repo = setup
    publish_empty_initial(repo)
    database.products.insert_one({"_id": "Q", "title": "Other", "provenance": {}})
    provider = Provider()
    service = SummaryService(database, repo, provider)
    settings = Settings(mongo_uri="mongodb://127.0.0.1:27032", mongo_database=database.name,
                        reviewer_token="reviewer", pm_token="pm")
    api = TestClient(create_app(settings, SimpleNamespace(summaries=service)))
    pm = {"Authorization": "Bearer pm"}
    assert api.get("/api/v1/products/P/summary", headers=pm).json()["current"]["version"] == 1
    assert api.get("/api/v1/products/Q/summary", headers=pm).json()["current"] is None
    assert api.get("/api/v1/products/P/summary", headers={"Authorization": "Bearer reviewer"}).status_code == 403
    assert provider.calls == 0
    assert api.patch("/api/v1/products/P/summary/settings", headers=pm,
                     json={"update_threshold": 3}).json()["update_threshold"] == 3
    refresh = {**pm, "Idempotency-Key": "retry-1"}
    assert api.post("/api/v1/products/P/summary/refresh", headers=refresh,
                    json={"reason": "pending_reviews"}).status_code == 202
    assert api.post("/api/v1/products/P/summary/refresh", headers=refresh,
                    json={"reason": "pending_reviews"}).status_code == 202
    assert api.post("/api/v1/products/P/summary/refresh", headers=refresh,
                    json={"reason": "guidance"}).status_code == 409
    history = api.get("/api/v1/products/P/summary/history?limit=1", headers=pm).json()
    assert [item["version"] for item in history["items"]] == [1]
    assert api.get("/api/v1/products/P/summary/versions/2", headers=pm).status_code == 404
    answer = api.post("/api/v1/products/P/summary/questions", headers=pm,
                      json={"version": 1, "question": "What changed?"}).json()
    assert answer["insufficient_evidence"] is True
    assert provider.calls == 0


def test_guidance_respects_membership_and_claim_boundary(setup):
    database, repo = setup
    publish_empty_initial(repo)
    now = datetime.now(timezone.utc)
    review = {"_id": "eligible", "parent_asin": "P", "source": "user_submission",
              "timestamp": now, "text": "Included feedback"}
    repo.admit_review(review)
    def decision(identifier, evidence, product="P"):
        database.decisions.insert_one({"_id": identifier, "parent_asin": product,
            "kind": "preference", "rationale": "Synthetic guidance " + identifier,
            "evidence_ids": evidence, "decided_at": now, "available_through": now})
        repo.request_refresh(product, "decision:" + identifier, "guidance")
    decision("included", ["eligible"])
    decision("heldout", ["excluded-heldout"])
    decision("other-product", [], product="Q")
    worker = SummaryWorker(database, repo, Provider(), memory=Memory())
    claim = repo.claim("P", now, 180)
    # Arrives after the claim's persisted admission boundary.
    decision("late", ["eligible"])
    selected = worker._guidance(claim, {"eligible"})
    assert [item["id"] for item in selected] == ["included"]
    frozen = repo.freeze(claim, [], [item["id"] for item in selected])
    assert frozen.guidance_ids == ["included"]
    assert "_refresh:decision:late" not in frozen.refresh_ids
