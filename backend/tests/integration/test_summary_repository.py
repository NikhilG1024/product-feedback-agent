"""Crash and replay behavior against a disposable Mongo database."""

from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from pymongo import MongoClient

from app.migrations.v3 import migrate
from app.summaries.contracts import GeneratedSummary, SemanticReview, SummaryCoverage, SummaryVersion
from app.summaries.repository import SummaryRepository, artifact_sha256


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    client = MongoClient(os.environ["TEST_MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=2000)
    database = client["test_summary_repository_" + uuid4().hex]
    migrate(database, False)
    yield SummaryRepository(database)
    client.drop_database(database.name)
    client.close()


def review(number, *, product="P", stamp=NOW):
    return {"_id": f"r{number:03}", "parent_asin": product, "source": "user_submission",
            "timestamp": stamp, "text": f"Review {number}"}


def candidate(claim, frozen, *, kind=None):
    version = SummaryVersion(product_id=claim.product_id, version=frozen.version,
        parent_version=claim.parent_version, job_id=claim.job_id,
        kind=kind or ("initial" if claim.parent_version is None else "reviews"),
        narrative="Useful account of feedback", themes=[],
        coverage=SummaryCoverage(historical_sample_count=0,
            new_review_count=len(frozen.review_ids)),
        delta_review_ids=frozen.review_ids, manifest_ref=None,
        model_identity="local/pinned", prompt_version="v1",
        guidance_references=frozen.guidance_ids, created_at=NOW)
    if claim.parent_version is None:
        version.semantic_review.status = "approved"
        version.semantic_review.reviewer_id = "reviewer-1"
        version.semantic_review.reviewer_type = "human"
        version.semantic_review.reviewed_at = NOW
        version.semantic_review.rubric_version = "v1"
        version.semantic_review.factual_support = True
        version.semantic_review.coverage = True
        version.semantic_review.classification = True
        version.semantic_review.artifact_sha256 = artifact_sha256(version)
    return version


def publish_initial(repo, review_ids=()):
    claim = repo.claim("P", NOW, 30)
    frozen = repo.freeze(claim, list(review_ids), [])
    version_id = repo.stage(claim, candidate(claim, frozen))
    assert repo.publish(claim, version_id, NOW)
    return claim


def test_unique_admission_and_threshold_count_unique_pending(repo):
    publish_initial(repo)
    repo.database.product_summary_state.update_one({"_id": "P"}, {"$set": {"update_threshold": 2}})
    repo.admit_review(review(1))
    repo.admit_review(review(1))
    assert repo.current("P").pending_review_count == 1
    assert repo.claim("P", NOW + timedelta(seconds=1), 30) is None
    repo.admit_review(review(2))
    assert repo.current("P").pending_review_count == 2
    assert repo.claim("P", NOW + timedelta(seconds=1), 30) is not None


def test_freeze_orders_timestamp_ties_by_id_and_later_arrivals_wait(repo):
    publish_initial(repo)
    repo.admit_review(review(2))
    repo.admit_review(review(1))
    claim = repo.claim("P", NOW + timedelta(seconds=1), 30)
    frozen = repo.freeze(claim, [], [])
    assert frozen.review_ids == ["r001", "r002"]
    repo.admit_review(review(3))
    assert repo.freeze(claim, [], []).review_ids == ["r001", "r002"]


def test_staged_orphans_are_hidden_and_retry_reuses_job_and_version(repo):
    publish_initial(repo)
    repo.admit_review(review(1))
    first = repo.claim("P", NOW + timedelta(seconds=1), 5)
    frozen = repo.freeze(first, [], [])
    version_id = repo.stage(first, candidate(first, frozen))
    assert repo.current("P").current.version == 1
    assert [v.version for v in repo.history("P", None, 10).items] == [1]
    assert repo.version("P", frozen.version) is None
    retry = repo.claim("P", NOW + timedelta(seconds=7), 5)
    assert retry.job_id == first.job_id
    assert repo.freeze(retry, [], []).review_ids == ["r001"]
    assert repo.stage(retry, candidate(retry, frozen)) == version_id
    assert not repo.publish(first, version_id, NOW + timedelta(seconds=7))
    assert repo.publish(retry, version_id, NOW + timedelta(seconds=7))
    assert repo.current("P").pending_review_count == 0


def test_pointer_publication_reconciles_ledger_without_double_count(repo):
    publish_initial(repo)
    repo.admit_review(review(1))
    claim = repo.claim("P", NOW + timedelta(seconds=1), 30)
    frozen = repo.freeze(claim, [], [])
    version_id = repo.stage(claim, candidate(claim, frozen))
    # Simulate a crash after the pointer CAS but before ledger and metadata repair.
    repo.database.product_summary_state.update_one({"_id": "P"},
        {"$set": {"current_version": frozen.version}})
    assert repo.current("P").current.version == frozen.version
    assert repo.current("P").pending_review_count == 0
    assert repo.database.product_summary_inputs.find_one({"review_id": "r001"})["incorporated_version"] == frozen.version
    assert not repo.publish(claim, version_id, NOW + timedelta(seconds=1))
    repo.admit_review(review(1))
    assert repo.current("P").pending_review_count == 0


def test_initial_requires_matching_approved_semantic_review(repo):
    claim = repo.claim("P", NOW, 30)
    frozen = repo.freeze(claim, [], [])
    draft = candidate(claim, frozen)
    draft.semantic_review.artifact_sha256 = "0" * 64
    version_id = repo.stage(claim, draft)
    assert not repo.publish(claim, version_id, NOW)
    assert repo.current("P").current is None


def test_pending_initial_artifact_can_be_reviewed_then_published(repo):
    claim = repo.claim("P", NOW, 30)
    frozen = repo.freeze(claim, [], [])
    draft = candidate(claim, frozen)
    draft.semantic_review = SemanticReview()
    version_id = repo.stage(claim, draft)
    assert not repo.publish(claim, version_id, NOW)
    review_result = SemanticReview(status="approved", reviewer_id="quality-1",
        reviewer_type="automated", reviewed_at=NOW, artifact_sha256=artifact_sha256(draft),
        rubric_version="v1", factual_support=True, coverage=True, classification=True)
    assert repo.apply_semantic_review("P", version_id, review_result)
    assert repo.publish(claim, version_id, NOW)
    assert repo.current("P").current.semantic_review.reviewer_type == "automated"


def test_threshold_100_drains_five_bounded_jobs_without_new_arrivals(repo):
    publish_initial(repo)
    repo.set_threshold("P", 100)
    for index in range(100):
        repo.admit_review(review(index))
    assert repo.claim("P", NOW + timedelta(seconds=1), 30) is not None
    for batch in range(5):
        claim = repo.claim("P", NOW + timedelta(seconds=batch + 1), 30) if batch else None
        if batch == 0:
            # The previous eligibility probe held the lease; expire it for retry.
            claim = repo.claim("P", NOW + timedelta(seconds=32), 30)
        assert claim is not None
        frozen = repo.freeze(claim, [], [])
        assert len(frozen.review_ids) == 20
        version_id = repo.stage(claim, candidate(claim, frozen))
        assert repo.publish(claim, version_id, claim.lease_expires_at - timedelta(seconds=1))
    assert repo.current("P").pending_review_count == 0
    assert repo.current("P").current.coverage.new_review_count == 20


def test_refresh_replay_and_checkpoint_survive_lease_retry(repo):
    publish_initial(repo)
    repo.set_threshold("P", 100)
    repo.admit_review(review(1))
    assert repo.request_refresh("P", "flush-1", "pending_reviews") is True
    assert repo.request_refresh("P", "flush-1", "pending_reviews") is False
    with pytest.raises(ValueError):
        repo.request_refresh("P", "flush-1", "guidance")
    claim = repo.claim("P", NOW + timedelta(seconds=1), 5)
    frozen = repo.freeze(claim, [], [])
    assert frozen.review_ids == ["r001"]
    repo.checkpoint(claim, {"step": 2})
    retry = repo.claim("P", NOW + timedelta(seconds=7), 5)
    assert retry.job_id == claim.job_id
    assert repo.get_checkpoint(retry) == {"step": 2}


def test_recovery_reads_remain_available_with_capacity_exhausted(repo):
    publish_initial(repo)
    repo.admit_review(review(1))
    claim = repo.claim("P", NOW + timedelta(seconds=1), 30)
    frozen = repo.freeze(claim, [], [])
    version_id = repo.stage(claim, candidate(claim, frozen))
    repo.database.product_summary_state.update_one({"_id": "P"},
        {"$set": {"current_version": frozen.version}})

    class Exhausted:
        def check_documents(self, documents):
            raise RuntimeError("capacity exhausted")

    repo.capacity = Exhausted()
    assert repo.current("P").current.version == 2
    assert repo.summary_status("P", "r001")["version"] == 2
    assert repo.version("P", 2).version == 2


def test_two_pilot_drafts_are_durable_but_only_approved_selected_draft_is_public(repo):
    generated = GeneratedSummary(narrative="A sound pilot", themes=[])
    raw = generated.model_dump_json().encode("utf-8")
    first = SummaryVersion(product_id="P", version=1, parent_version=None, job_id="pilot-a",
        kind="initial", narrative=generated.narrative, themes=[],
        coverage=SummaryCoverage(historical_sample_count=0, new_review_count=0),
        delta_review_ids=[], model_identity="pinned", prompt_version="v1",
        guidance_references=[], created_at=NOW)
    second = first.model_copy(update={"job_id": "pilot-b", "narrative": "Another pilot"})
    second_raw = GeneratedSummary(narrative="Another pilot", themes=[]).model_dump_json().encode("utf-8")
    first_id = repo.stage_initial_draft("P", first, raw, {"run": "one"})
    second_id = repo.stage_initial_draft("P", second, second_raw, {"run": "one"})
    assert first_id != second_id
    assert repo.stage_initial_draft("P", first, raw, {"run": "one"}) == first_id
    assert repo.current("P").current is None
    assert repo.history("P", None, 10).items == []
    review_result = SemanticReview(status="approved", reviewer_id="human-1",
        reviewer_type="human", reviewed_at=NOW,
        artifact_sha256=__import__("hashlib").sha256(second_raw).hexdigest(),
        rubric_version="v1", factual_support=True, coverage=True, classification=True)
    assert repo.apply_semantic_review("P", second_id, review_result)
    claim = repo.claim_initial_candidate("P", second_id, NOW, 30)
    assert repo.publish(claim, second_id, NOW)
    assert repo.current("P").current.job_id == "pilot-b"
    assert repo.version("P", repo.draft(first_id).version) is None
    assert [v.job_id for v in repo.history("P", None, 10).items] == ["pilot-b"]


def test_draining_eligible_boundary_leaves_new_arrival_for_later_threshold(repo):
    publish_initial(repo)
    repo.set_threshold("P", 21)
    for index in range(21):
        repo.admit_review(review(index))
    claim = repo.next_claim(NOW + timedelta(seconds=1), 30)
    first = repo.freeze(claim, [], [])
    assert len(first.review_ids) == 20
    repo.admit_review(review(21))
    assert repo.publish(claim, repo.stage(claim, candidate(claim, first)), NOW + timedelta(seconds=1))
    follow_on = repo.next_claim(NOW + timedelta(seconds=2), 30)
    remaining = repo.freeze(follow_on, [], [])
    assert remaining.review_ids == ["r020"]
    assert repo.publish(follow_on, repo.stage(follow_on, candidate(follow_on, remaining)),
                        NOW + timedelta(seconds=2))
    assert repo.current("P").pending_review_count == 1
    assert repo.next_claim(NOW + timedelta(seconds=3), 30) is None


def test_lease_can_be_renewed_repeatedly_past_original_expiry(repo):
    publish_initial(repo)
    repo.admit_review(review(1))
    claim = repo.claim("P", NOW + timedelta(seconds=1), 5)
    assert repo.renew(claim, NOW + timedelta(seconds=5))
    assert repo.renew(claim, NOW + timedelta(seconds=9))
    frozen = repo.freeze(claim, [], [])
    version_id = repo.stage(claim, candidate(claim, frozen))
    assert repo.publish(claim, version_id, NOW + timedelta(seconds=12))


def test_capacity_stops_new_ledger_growth_without_blocking_existing_reads(repo):
    publish_initial(repo)

    class Exhausted:
        def check_documents(self, documents):
            raise RuntimeError("capacity exhausted")

    repo.capacity = Exhausted()
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        repo.admit_review(review(1))
    assert repo.database.product_summary_inputs.count_documents({"product_id": "P"}) == 0
    assert repo.current("P").current.version == 1


def test_memory_sync_retries_per_published_version_independent_of_summary(repo):
    publish_initial(repo)
    assert repo.current("P").status == "ready"
    assert repo.current("P").memory_status == "pending"
    claim = repo.claim_memory_sync(NOW + timedelta(seconds=1), 5)
    assert claim.idempotency_key == "summary:P:v1"
    assert repo.fail_memory_sync(claim, "memory_unavailable", NOW + timedelta(seconds=2))
    assert repo.current("P").status == "ready"
    assert repo.current("P").memory_status == "failed"
    assert repo.claim_memory_sync(NOW + timedelta(seconds=3), 5) is None
    retry = repo.claim_memory_sync(NOW + timedelta(seconds=33), 5)
    assert retry.idempotency_key == claim.idempotency_key
    assert repo.complete_memory_sync(retry, NOW + timedelta(seconds=34))
    assert repo.current("P").memory_status == "synced"


def test_memory_sync_checkpoint_survives_expired_owner_and_fences_completion(repo):
    publish_initial(repo)
    first = repo.claim_memory_sync(NOW + timedelta(seconds=1), 5)
    repo.memory_sync_checkpoint(first, {"retain_id": first.idempotency_key, "phase": "sent"})
    retry = repo.claim_memory_sync(NOW + timedelta(seconds=7), 5)
    assert retry.idempotency_key == first.idempotency_key
    assert repo.get_memory_sync_checkpoint(retry) == {"retain_id": "summary:P:v1", "phase": "sent"}
    assert not repo.complete_memory_sync(first, NOW + timedelta(seconds=7))
    assert repo.complete_memory_sync(retry, NOW + timedelta(seconds=8))
