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


def test_saved_review_without_published_initial_reports_initialization_needed(setup):
    database, repo = setup
    reviews = ReviewService(database, summaries=repo)
    principal = Principal(user_id="reviewer", role="reviewer")
    saved = reviews.submit("P", principal, "initial-needed",
        ReviewInput(title="New feedback", text="The hinge broke", rating=2))
    assert saved["summary"] == {"status": "needs_initial_summary", "version": None}
    assert reviews.status(saved["id"], principal)["summary"] == saved["summary"]


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


@pytest.mark.parametrize("decision_count", [21, 101])
def test_guidance_consumes_exact_oldest_refresh_batches(setup, decision_count):
    database, repo = setup
    publish_empty_initial(repo)
    now = datetime.now(timezone.utc)
    worker = SummaryWorker(database, repo, Provider(), memory=Memory())
    for index in range(decision_count):
        identifier = f"decision-{index:03d}"
        database.decisions.insert_one({"_id": identifier, "parent_asin": "P",
            "kind": "preference", "rationale": "Synthetic guidance " + identifier,
            "evidence_ids": [], "decided_at": now, "available_through": now,
            "summary_refresh_outstanding": False})
        repo.request_refresh("P", "decision:" + identifier, "guidance")
    consumed = []
    used = []
    while len(consumed) < decision_count:
        claim = repo.claim("P", datetime.now(timezone.utc), 180)
        assert claim is not None
        guidance = worker._guidance(claim, set())
        frozen = repo.freeze(claim, [], [item["id"] for item in guidance])
        batch = [marker.removeprefix("_refresh:decision:") for marker in frozen.refresh_ids]
        assert frozen.guidance_ids == batch
        consumed.extend(batch)
        used.extend(frozen.guidance_ids)
        # Simulate the completed generation/publication so the next claim can
        # consume the remaining durable markers.
        parent = repo.version("P", claim.parent_version)
        version = parent.model_copy(update={"version": frozen.version,
            "parent_version": claim.parent_version, "job_id": claim.job_id,
            "kind": "guidance", "guidance_references": frozen.guidance_ids,
            "published_at": None, "created_at": datetime.now(timezone.utc)})
        version.semantic_review.status = "pending"
        version.semantic_review.reviewer_id = None
        version.semantic_review.reviewer_type = None
        version.semantic_review.reviewed_at = None
        version.semantic_review.rubric_version = None
        version.semantic_review.factual_support = None
        version.semantic_review.coverage = None
        version.semantic_review.classification = None
        version.semantic_review.artifact_sha256 = None
        version_id = repo.stage(claim, version)
        assert repo.publish(claim, version_id, datetime.now(timezone.utc))
    expected = [f"decision-{index:03d}" for index in range(decision_count)]
    assert consumed == expected
    assert used == expected


def test_guidance_batch_counts_mixed_refresh_markers(setup):
    database, repo = setup
    publish_empty_initial(repo)
    now = datetime.now(timezone.utc)
    for index in range(20):
        identifier = f"decision-{index:03d}"
        database.decisions.insert_one({"_id": identifier, "parent_asin": "P",
            "kind": "preference", "rationale": "Synthetic guidance " + identifier,
            "evidence_ids": [], "decided_at": now, "available_through": now,
            "summary_refresh_outstanding": False})
        repo.request_refresh("P", "decision:" + identifier, "guidance")
        if index == 9:
            repo.request_refresh("P", "manual-flush", "pending_reviews")
    claim = repo.claim("P", datetime.now(timezone.utc), 180)
    assert claim is not None
    selected = SummaryWorker(database, repo, Provider(), memory=Memory())._guidance(claim, set())
    frozen = repo.freeze(claim, [], [item["id"] for item in selected])
    assert len(frozen.refresh_ids) == 20
    assert "_refresh:manual-flush" in frozen.refresh_ids
    assert frozen.guidance_ids == [f"decision-{index:03d}" for index in range(19)]
    assert "_refresh:decision:decision-019" not in frozen.refresh_ids


def test_late_insert_at_reserved_sequence_is_not_consumed_without_guidance(setup):
    database, repo = setup
    publish_empty_initial(repo)
    now = datetime.now(timezone.utc)
    database.decisions.insert_one({"_id": "late-sequence", "parent_asin": "P",
        "kind": "preference", "rationale": "Synthetic late guidance", "evidence_ids": [],
        "decided_at": now, "available_through": now,
        "summary_refresh_outstanding": False})
    repo.request_refresh("P", "existing-flush", "pending_reviews")
    # A concurrent request reserves its sequence before inserting its ledger
    # row. The worker claims during that gap and snapshots no refresh marker.
    repo.states.update_one({"_id": "P"}, {"$inc": {"admission_sequence": 1}})
    reserved = repo.states.find_one({"_id": "P"})["admission_sequence"]
    claim = repo.claim("P", now, 180)
    assert claim is not None
    refresh_ids = repo.pending_refresh_ids(claim)
    assert refresh_ids == ["_refresh:existing-flush"]
    worker = SummaryWorker(database, repo, Provider(), memory=Memory())
    assert worker._guidance(claim, set(), refresh_ids) == []
    database.product_summary_inputs.insert_one({"_id": uuid4().hex, "product_id": "P",
        "review_id": "_refresh:decision:late-sequence", "source": "refresh_guidance",
        "admission_sequence": reserved, "admitted_at": now, "source_timestamp": now,
        "incorporated_version": None})
    frozen = repo.freeze(claim, [], [], refresh_ids=refresh_ids)
    assert frozen.refresh_ids == ["_refresh:existing-flush"]
    assert repo.pending_refresh_ids(claim) == refresh_ids  # Frozen snapshot is stable.
    assert database.product_summary_inputs.find_one({"review_id":
        "_refresh:decision:late-sequence"})["incorporated_version"] is None


def test_redundant_refresh_completes_without_model_or_new_version(setup):
    database, repo = setup
    publish_empty_initial(repo)
    provider = Provider()
    worker = SummaryWorker(database, repo, provider, memory=Memory())
    repo.request_refresh("P", "already-covered", "pending_reviews")
    assert worker.tick() is True
    assert provider.calls == 0
    assert repo.current("P").current.version == 1
    assert repo.current("P").status == "ready"
    assert repo.versions.count_documents({"product_id": "P"}) == 1
    assert repo.inputs.find_one({"review_id": "_refresh:already-covered"})["incorporated_version"] == 1
    assert worker.tick() is False
    assert repo.states.find_one({"_id": "P"})["drain_boundary"] is None
    repo.admit_review({"_id": "later-review", "parent_asin": "P", "source": "user_submission",
                       "timestamp": datetime.now(timezone.utc), "text": "Later feedback"})
    assert repo.claim("P", datetime.now(timezone.utc), 180) is not None


def test_failed_frozen_no_input_refresh_recovers_as_noop(setup):
    database, repo = setup
    publish_empty_initial(repo)
    repo.request_refresh("P", "retry-covered", "pending_reviews")
    now = datetime.now(timezone.utc)
    claim = repo.claim("P", now, 180)
    frozen = repo.freeze(claim, [], [])
    assert frozen.review_ids == [] and frozen.guidance_ids == []
    repo.fail(claim, "summary_no_inputs", now)
    repo.states.update_one({"_id": "P"}, {"$set": {"next_attempt_at": now}})
    provider = Provider()
    worker = SummaryWorker(database, repo, provider, memory=Memory())
    assert worker.tick() is True
    assert provider.calls == 0
    assert repo.current("P").current.version == 1
    assert repo.current("P").status == "ready"
    assert repo.versions.count_documents({"product_id": "P"}) == 1
    assert repo.inputs.find_one({"review_id": "_refresh:retry-covered"})["incorporated_version"] == 1
