"""Synthetic cutover rehearsal against disposable Mongo; never the application DB."""

import hashlib
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.config import Settings
from app.domain import Principal, ReviewInput
from app.main import create_app
from app.migrations.v2 import migrate as migrate_v2
from app.migrations.v3 import migrate as migrate_v3
from app.services.reviews import ReviewService
from app.summaries.contracts import GeneratedSummary, SummaryVersion
from app.summaries.repository import SummaryRepository
from app.summaries.service import SummaryService
from app.summaries.worker import SummaryWorker


@pytest.fixture
def database():
    client = MongoClient(os.environ["TEST_MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=2000)
    db = client["test_summary_acceptance_" + uuid4().hex]
    migrate_v2(db, False)
    migrate_v3(db, False)
    yield db
    client.drop_database(db.name)
    client.close()


class SyntheticProvider:
    model = "synthetic/local-only"
    def __init__(self):
        self.calls = 0

    def generate_summary(self, messages, output_schema):
        self.calls += 1
        data = json.loads(messages[1]["content"])
        old = data["previous"] or {"themes": [], "contradictions": []}
        themes = list(old["themes"])
        for review in data["new_reviews"]:
            themes.append({"id": review["id"], "description": review["title"],
                "issue_type": "other", "polarity": "neutral",
                "evidence": [{"review_id": review["id"], "quote": review["text"]}]})
        return {"narrative": "Synthetic summary with " + str(len(themes)) + " cited themes",
                "themes": themes, "contradictions": old.get("contradictions", [])}


class SyntheticMemory:
    def __init__(self):
        self.fail = False
        self.ids = []

    def ensure_retained(self, bank_id, record, *, state=None, checkpoint=None):
        from app.integrations.memory import ProviderError
        self.ids.append(record.id)
        checkpoint({"record_id": record.id, "status": "pending"})
        if self.fail:
            raise ProviderError("memory_unavailable")


def approved_initial_fixture(database, repo):
    """Stage and approve one synthetic historical sample through the real gate."""
    now = datetime.now(timezone.utc)
    historical = {"_id": "sample-r1", "parent_asin": "P", "asin": "P",
        "title": "Initial feedback", "text": "The sound is clear.", "rating": 5,
        "timestamp": now, "timestamp_ms": int(now.timestamp() * 1000),
        "batch": "B", "batch_id": "synthetic:B", "held_out": False,
        "dataset_id": "synthetic", "provenance": {"kind": "synthetic"}}
    database.reviews.insert_one(historical)
    historical = database.reviews.find_one({"_id": historical["_id"]})
    content = GeneratedSummary(narrative="Initial synthetic feedback is positive.", themes=[{
        "id": "sound", "description": "Clear sound", "issue_type": "preference",
        "polarity": "positive", "evidence": [{"review_id": historical["_id"],
            "quote": historical["title"]}]}])
    candidate = SummaryVersion(**content.model_dump(), product_id="P", version=1,
        parent_version=None, job_id="synthetic-initial", kind="initial",
        coverage={"historical_sample_count": 1, "new_review_count": 0},
        delta_review_ids=[historical["_id"]], manifest_ref="synthetic-manifest",
        model_identity="synthetic/local-only", prompt_version="synthetic-v1",
        guidance_references=[], created_at=now)
    raw = content.model_dump_json().encode()
    repo.admit_initial_reviews("P", [historical])
    version_id = repo.stage_initial_draft("P", candidate, raw, {"kind": "synthetic-fixture"})
    assert repo.current("P").current is None
    review = {"status": "approved", "reviewer_id": "synthetic-auditor",
        "reviewer_type": "human", "reviewed_at": now,
        "artifact_sha256": hashlib.sha256(raw).hexdigest(), "rubric_version": "synthetic-v1",
        "factual_support": True, "coverage": True, "classification": True}
    assert repo.apply_semantic_review("P", version_id, review)
    claim = repo.claim_initial_candidate("P", version_id, now, 180)
    assert repo.publish(claim, version_id, now)
    return historical


def test_synthetic_summary_cutover_acceptance(database):
    database.products.insert_one({"_id": "P", "title": "Synthetic headphones", "provenance": {}})
    repo = SummaryRepository(database)
    historical = approved_initial_fixture(database, repo)
    provider, memory = SyntheticProvider(), SyntheticMemory()
    worker = SummaryWorker(database, repo, provider, memory=memory)
    settings = Settings(mongo_uri="mongodb://127.0.0.1:27032", mongo_database=database.name,
        reviewer_token="synthetic-reviewer", pm_token="synthetic-pm")
    api = TestClient(create_app(settings, SimpleNamespace(
        summaries=SummaryService(database, repo, provider))))
    pm = {"Authorization": "Bearer synthetic-pm"}
    initial = api.get("/api/v1/products/P/summary", headers=pm).json()
    assert initial["current"]["version"] == 1
    assert initial["current"]["coverage"] == {"historical_sample_count": 1, "new_review_count": 0}
    assert provider.calls == 0
    reviewer = Principal(user_id="synthetic-reviewer", role="reviewer")
    reviews = ReviewService(database, summaries=repo)
    first = reviews.submit("P", reviewer, "new-1", ReviewInput(title="Battery", text="Battery fades fast.", rating=2))
    assert first["summary"]["status"] in {"queued", "waiting"}
    assert worker.tick()
    version_two = repo.current("P").current
    assert version_two.version == 2
    assert version_two.coverage.new_review_count == 1
    assert historical["title"] == repo.version("P", 1).themes[0].evidence[0].quote
    assert repo.version("P", 1).narrative == "Initial synthetic feedback is positive."
    assert provider.calls == 1
    assert [item.version for item in repo.history("P", None, 10).items] == [2, 1]
    assert api.get("/api/v1/products/P/summary/history", headers=pm).status_code == 200
    assert provider.calls == 1

    assert api.patch("/api/v1/products/P/summary/settings", headers=pm,
                     json={"update_threshold": 3}).json()["update_threshold"] == 3
    for i in (2, 3):
        reviews.submit("P", reviewer, f"new-{i}", ReviewInput(title=f"Issue {i}",
            text=f"Synthetic issue {i}.", rating=2))
    assert repo.current("P").pending_review_count == 2
    assert worker.tick()  # memory sync may run; no generation below threshold
    assert provider.calls == 1
    assert repo.current("P").current.version == 2
    flush = api.post("/api/v1/products/P/summary/refresh", headers={**pm,
        "Idempotency-Key": "synthetic-flush"}, json={"reason": "pending_reviews"})
    assert flush.status_code == 202
    assert worker.tick()
    version_three = repo.current("P").current
    assert version_three.version == 3
    assert version_three.coverage.model_dump() == {"historical_sample_count": 1, "new_review_count": 3}
    assert repo.version("P", 2).narrative == version_two.narrative

    now = datetime.now(timezone.utc)
    database.decisions.insert_one({"_id": "synthetic-guidance", "parent_asin": "P",
        "kind": "preference", "rationale": "Keep battery complaints visible.",
        "evidence_ids": [], "available_through": now, "decided_at": now,
        "summary_refresh_outstanding": True})
    assert worker.tick()
    version_four = repo.current("P").current
    assert version_four.version == 4 and version_four.kind == "guidance"
    assert version_four.coverage == version_three.coverage
    assert version_four.guidance_references == ["synthetic-guidance"]
    assert [item.version for item in repo.history("P", None, 10).items] == [4, 3, 2, 1]

    # The memory outbox is independent: a failed sync leaves version 4 published.
    memory.fail = True
    assert worker.sync_memory_once()
    assert repo.current("P").current.version == 4
    assert repo.current("P").memory_status == "failed"
    database.product_summary_versions.update_one({"product_id": "P", "version": 4},
        {"$set": {"memory_sync.next_attempt_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}})
    memory.fail = False
    assert worker.sync_memory_once()
    assert repo.current("P").memory_status == "synced"
    assert memory.ids.count("summary:P:v4") == 2
