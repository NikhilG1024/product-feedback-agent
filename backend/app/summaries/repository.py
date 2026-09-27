"""Durable summary input ledger and fenced, parent-linked publication.

Only the current pointer makes a version public. Other collections are repaired
from that pointer's parent chain, so cross-collection transactions are unnecessary.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.repositories.capacity import CapacityGuard
from app.errors import ServiceError
from app.summaries.contracts import GeneratedSummary, HistoryPage, SemanticReview, SummaryVersion, SummaryView


@dataclass(frozen=True)
class SummaryClaim:
    product_id: str
    owner_token: str
    lease_expires_at: datetime
    parent_version: int | None
    job_id: str


@dataclass(frozen=True)
class FrozenUpdate:
    product_id: str
    job_id: str
    parent_version: int | None
    version: int
    review_ids: list[str]
    guidance_ids: list[str]
    refresh_ids: list[str]


@dataclass(frozen=True)
class MemorySyncClaim:
    product_id: str
    version: int
    owner_token: str
    idempotency_key: str
    lease_expires_at: datetime


def artifact_sha256(version: SummaryVersion | dict) -> str:
    """Digest of the immutable candidate, excluding review and publication marks."""
    raw = version.model_dump(mode="python") if isinstance(version, SummaryVersion) else dict(version)
    for key in ("_id", "semantic_review", "published_at"):
        raw.pop(key, None)
    # A model round-trip gives Mongo's UTC datetime and an in-memory candidate
    # the same canonical representation.
    raw = {key: value for key, value in raw.items() if key in SummaryVersion.model_fields}
    stamp = raw.get("created_at")
    if isinstance(stamp, datetime):
        raw["created_at"] = stamp.replace(microsecond=stamp.microsecond // 1000 * 1000)
    document = SummaryVersion.model_validate(raw).model_dump(mode="json", exclude={"semantic_review", "published_at"})
    payload = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _public_version(document):
    return SummaryVersion.model_validate({key: value for key, value in document.items()
                                          if key in SummaryVersion.model_fields})


def _review_digest(document):
    return document.get("raw_artifact_sha256") or artifact_sha256(document)


class SummaryRepository:
    def __init__(self, database, capacity=None):
        self.database = database
        self.capacity = capacity if capacity is not None else CapacityGuard(database)

    @property
    def states(self):
        return self.database.product_summary_state

    @property
    def versions(self):
        return self.database.product_summary_versions

    @property
    def inputs(self):
        return self.database.product_summary_inputs

    @staticmethod
    def _memory_sync(product_id, version):
        return {"status": "pending", "idempotency_key": f"summary:{product_id}:v{version}",
                "attempts": 0, "owner_token": None, "lease_expires_at": None,
                "next_attempt_at": None, "error_code": None, "synced_at": None}

    def _require_migrated(self):
        if self.database.schema_migrations.find_one({"_id": "v3"}, {"_id": 1}) is None:
            raise ServiceError("summary_schema_unavailable", 503)

    @staticmethod
    def _fresh_state(product_id):
        return {"_id": product_id, "product_id": product_id, "current_version": None,
                "next_version": 1, "update_threshold": 1, "status": "uninitialized",
                "fence": 0, "admission_sequence": 0, "job": None,
                "drain_boundary": None, "memory_status": "unknown"}

    def _state(self, product_id):
        self._require_migrated()
        state = self.states.find_one({"_id": product_id})
        if state is not None:
            return state
        fresh = self._fresh_state(product_id)
        self.capacity.check_documents([fresh])
        try:
            self.states.insert_one(fresh)
            return fresh
        except DuplicateKeyError:
            return self.states.find_one({"_id": product_id})

    def admit_review(self, review) -> None:
        product_id = review["parent_asin"]
        review_id = str(review["_id"])
        if self.inputs.find_one({"product_id": product_id, "review_id": review_id}, {"_id": 1}):
            return
        self._state(product_id)
        source = "user_submission" if review.get("source") == "user_submission" else "initial"
        timestamp = review.get("timestamp") or review.get("created_at") or datetime.now(timezone.utc)
        self.capacity.check_documents([{"product_id": product_id, "review_id": review_id,
                                        "source_timestamp": timestamp, "source": source}])
        state = self.states.find_one_and_update({"_id": product_id}, {"$inc": {"admission_sequence": 1}},
                                                return_document=ReturnDocument.AFTER)
        document = {"_id": uuid4().hex, "product_id": product_id, "review_id": review_id,
                    "source": source, "admitted_at": datetime.now(timezone.utc),
                    "source_timestamp": timestamp,
                    "admission_sequence": state["admission_sequence"], "incorporated_version": None}
        self.capacity.check_documents([document])
        try:
            self.inputs.insert_one(document)
        except DuplicateKeyError:
            # Another acknowledgement inserted this same review first. Gaps in
            # admission sequence are harmless; membership is the source of truth.
            pass

    def admit_initial_reviews(self, product_id, reviews) -> int:
        """Admit one verified, bounded initialization sample with one quota preflight."""
        selected = list(reviews)
        if len(selected) > 20:
            raise ValueError("initial sample must contain at most 20 reviews")
        if not selected:
            return 0
        ids = [row.get("_id") for row in selected]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("initial sample IDs must be unique strings")
        self._require_migrated()
        stored = {row["_id"]: row for row in self.database.reviews.find(
            {"_id": {"$in": ids}, "parent_asin": product_id})}
        for supplied in selected:
            row = stored.get(supplied["_id"])
            if (row is None or supplied.get("parent_asin") != product_id or
                    row.get("source", "amazon_2023") != "amazon_2023" or
                    row.get("held_out") is not False or row.get("batch") == "C" or
                    any(supplied.get(field) != row.get(field) for field in
                        ("timestamp", "title", "text", "rating"))):
                raise ValueError("initial sample contains an ineligible or changed review")
        existing = {row["review_id"]: row for row in self.inputs.find(
            {"product_id": product_id, "review_id": {"$in": ids}})}
        if any(row["source"] != "initial" for row in existing.values()):
            raise ValueError("initial sample conflicts with existing membership")
        fresh = [row for row in selected if row["_id"] not in existing]
        if not fresh:
            return 0
        now = datetime.now(timezone.utc)
        state = self.states.find_one({"_id": product_id})
        estimates = [{"_id": uuid4().hex, "product_id": product_id, "review_id": row["_id"],
                      "source": "initial", "admitted_at": now,
                      "source_timestamp": row["timestamp"], "admission_sequence": 0,
                      "incorporated_version": None} for row in fresh]
        self.capacity.check_documents(([self._fresh_state(product_id)] if state is None else []) + estimates)
        if state is None:
            try:
                self.states.insert_one(self._fresh_state(product_id))
            except DuplicateKeyError:
                pass
        updated = self.states.find_one_and_update({"_id": product_id},
            {"$inc": {"admission_sequence": len(estimates)}}, return_document=ReturnDocument.AFTER)
        start = updated["admission_sequence"] - len(estimates) + 1
        inserted = 0
        for index, document in enumerate(estimates):
            document["admission_sequence"] = start + index
            try:
                self.inputs.insert_one(document)
                inserted += 1
            except DuplicateKeyError:
                winner = self.inputs.find_one({"product_id": product_id,
                                               "review_id": document["review_id"]})
                if winner is None or winner["source"] != "initial":
                    raise ValueError("initial sample conflicts with existing membership") from None
        return inserted

    def _pending_query(self, product_id, *, boundary=None, source="user_submission"):
        query = {"product_id": product_id, "source": source, "incorporated_version": None}
        if boundary is not None:
            query["admission_sequence"] = {"$lte": boundary}
        return query

    def _pending_count(self, product_id, *, boundary=None):
        return self.inputs.count_documents(self._pending_query(product_id, boundary=boundary))

    def _refresh_query(self, product_id, boundary=None):
        query = {"product_id": product_id, "source": {"$in": ["refresh_pending_reviews", "refresh_guidance"]},
                 "incorporated_version": None}
        if boundary is not None:
            query["admission_sequence"] = {"$lte": boundary}
        return query

    def _refresh_count(self, product_id, *, boundary=None):
        return self.inputs.count_documents(self._refresh_query(product_id, boundary))

    def _eligible_count(self, product_id, *, boundary=None):
        return self._pending_count(product_id, boundary=boundary) + self._refresh_count(
            product_id, boundary=boundary)

    def set_threshold(self, product_id, threshold) -> None:
        if type(threshold) is not int or not 1 <= threshold <= 100:
            raise ValueError("threshold must be an integer from 1 to 100")
        self._state(product_id)
        eligible = self._pending_count(product_id) >= threshold or self._refresh_count(product_id) > 0
        state = self.states.find_one({"_id": product_id})
        status = "queued" if eligible and state.get("current_version") else state.get("status")
        self.states.update_one({"_id": product_id},
                               {"$set": {"update_threshold": threshold, "status": status}})

    def request_refresh(self, product_id, key, reason) -> bool:
        if (reason not in {"pending_reviews", "guidance"} or not isinstance(key, str)
                or not 1 <= len(key) <= 200):
            raise ValueError("invalid refresh request")
        review_id = "_refresh:" + key
        source = "refresh_" + reason
        existing = self.inputs.find_one({"product_id": product_id, "review_id": review_id})
        if existing:
            if existing["source"] != source:
                raise ValueError("idempotency key was used for another reason")
            return False
        self._state(product_id)
        now = datetime.now(timezone.utc)
        self.capacity.check_documents([{"product_id": product_id, "review_id": review_id,
                                        "source": source, "admitted_at": now}])
        state = self.states.find_one_and_update({"_id": product_id},
            {"$inc": {"admission_sequence": 1}}, return_document=ReturnDocument.AFTER)
        document = {"_id": uuid4().hex, "product_id": product_id, "review_id": review_id,
                    "source": source, "admitted_at": now, "source_timestamp": now,
                    "admission_sequence": state["admission_sequence"], "incorporated_version": None}
        self.capacity.check_documents([document])
        try:
            self.inputs.insert_one(document)
        except DuplicateKeyError:
            winner = self.inputs.find_one({"product_id": product_id, "review_id": review_id})
            if winner is None or winner["source"] != source:
                raise ValueError("idempotency key was used for another reason") from None
            return False
        if state.get("current_version"):
            self.states.update_one({"_id": product_id, "job": None}, {"$set": {"status": "queued"}})
        return True

    def next_claim(self, now, lease_seconds) -> SummaryClaim | None:
        # Initialization has its own pilot/review path. The incremental worker
        # considers only already published products.
        states = self.states.find({"current_version": {"$ne": None}},
                                  {"_id": 1, "next_attempt_at": 1}).sort("_id", 1)
        for state in states:
            if state.get("next_attempt_at") and state["next_attempt_at"] > now:
                continue
            claim = self.claim(state["_id"], now, lease_seconds)
            if claim is not None:
                return claim
        return None

    def checkpoint(self, claim, data) -> None:
        if not isinstance(data, dict):
            raise ValueError("checkpoint must be an object")
        state = self._owned(claim)
        if state is None:
            raise ValueError("claim is no longer current")
        self.capacity.check_documents([{"checkpoint": data}])
        updated = self.states.update_one({"_id": claim.product_id,
            "job.owner_token": claim.owner_token, "current_version": claim.parent_version},
            {"$set": {"job.checkpoint": data}})
        if updated.matched_count != 1:
            raise ValueError("claim changed during checkpoint")

    def get_checkpoint(self, claim):
        state = self._owned(claim)
        return state["job"].get("checkpoint") if state else None

    def fail(self, claim, code, now) -> None:
        if not isinstance(code, str) or not code:
            raise ValueError("error code required")
        self.states.update_one({"_id": claim.product_id, "job.owner_token": claim.owner_token,
                                "current_version": claim.parent_version},
            {"$set": {"status": "failed", "error_code": code,
                      "next_attempt_at": now + timedelta(seconds=30),
                      "job.lease_expires_at": now}})

    def summary_status(self, product_id, review_id) -> dict:
        self.reconcile(product_id)
        entry = self.inputs.find_one({"product_id": product_id, "review_id": review_id})
        if entry and entry.get("incorporated_version") is not None:
            return {"status": "incorporated", "version": entry["incorporated_version"]}
        if entry:
            return {"status": "pending", "version": None}
        return {"status": "untracked", "version": None}

    def pending_rows(self, claim) -> list[dict]:
        state = self._owned(claim)
        if state is None:
            raise ValueError("claim is no longer current")
        job = state["job"]
        ids = job.get("review_ids")
        if ids is None:
            query = self._pending_query(claim.product_id, boundary=job["boundary"],
                                        source="initial" if claim.parent_version is None else "user_submission")
            ids = [item["review_id"] for item in self.inputs.find(query)
                   .sort([("source_timestamp", 1), ("review_id", 1)]).limit(20)]
        rows = {str(row["_id"]): row for row in self.database.reviews.find(
            {"_id": {"$in": ids}, "parent_asin": claim.product_id})}
        return [rows[review_id] for review_id in ids if review_id in rows]

    def pending_refresh_ids(self, claim) -> list[str]:
        """Return the exact oldest refresh batch this claim will freeze."""
        state = self._owned(claim)
        if state is None:
            raise ValueError("claim is no longer current")
        job = state["job"]
        if job.get("refresh_ids") is not None:
            return list(job["refresh_ids"])
        return [item["review_id"] for item in self.inputs.find(
            self._refresh_query(claim.product_id, job["boundary"]))
            .sort("admission_sequence", 1).limit(20)]

    @staticmethod
    def _claim_from(state):
        job = state["job"]
        return SummaryClaim(state["product_id"], job["owner_token"], job["lease_expires_at"],
                            job["parent_version"], job["job_id"])

    def claim(self, product_id, now, lease_seconds) -> SummaryClaim | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        # A pointer can be durable while its ledger reconciliation was interrupted.
        # Repair before either the active-lease or threshold eligibility decision.
        self.reconcile(product_id)
        state = self._state(product_id)
        job = state.get("job")
        if job and job["lease_expires_at"] > now:
            return None
        if state.get("next_attempt_at") and state["next_attempt_at"] > now:
            return None
        if job and job["parent_version"] != state.get("current_version"):
            # A pointer moved after the old worker's claim. Its candidate is orphaned.
            self.reconcile(product_id)
            state = self.states.find_one({"_id": product_id})
            job = state.get("job")
            if job and job["lease_expires_at"] > now:
                return None
            if job and job["parent_version"] != state.get("current_version"):
                job = None
        boundary = state.get("drain_boundary")
        if job is None and state.get("current_version") is not None:
            if boundary is not None and self._eligible_count(product_id, boundary=boundary) == 0:
                boundary = None
            if (boundary is None and self._pending_count(product_id) < state.get("update_threshold", 1)
                    and self._refresh_count(product_id) == 0):
                return None
        if job is None:
            boundary = boundary if boundary is not None else state.get("admission_sequence", 0)
            job = {"job_id": uuid4().hex, "parent_version": state.get("current_version"),
                   "boundary": boundary, "review_ids": None, "guidance_ids": None,
                   "version": None}
            self.capacity.check_documents([{"job": {**job, "owner_token": "0" * 32,
                                                   "lease_seconds": lease_seconds,
                                                   "lease_expires_at": now}}])
        job = {**job, "owner_token": uuid4().hex, "lease_seconds": lease_seconds,
               "lease_expires_at": now + timedelta(seconds=lease_seconds)}
        # The monotonically increasing fence detects a concurrent claimant even
        # when both observed the same expired lease.
        updated = self.states.find_one_and_update(
            {"_id": product_id, "fence": state["fence"], "current_version": state.get("current_version")},
            {"$set": {"job": job, "drain_boundary": boundary, "status": "updating"},
             "$inc": {"fence": 1}}, return_document=ReturnDocument.AFTER)
        return self._claim_from(updated) if updated else None

    def _owned(self, claim, now=None):
        state = self.states.find_one({"_id": claim.product_id})
        if not state or not state.get("job"):
            return None
        job = state["job"]
        if (job["owner_token"] != claim.owner_token or job["job_id"] != claim.job_id or
                job["parent_version"] != claim.parent_version or
                state.get("current_version") != claim.parent_version):
            return None
        if now is not None and job["lease_expires_at"] <= now:
            return None
        return state

    def renew(self, claim, now) -> bool:
        state = self._owned(claim, now)
        if state is None:
            return False
        new_expiry = now + timedelta(seconds=state["job"]["lease_seconds"])
        result = self.states.update_one(
            {"_id": claim.product_id, "job.owner_token": claim.owner_token,
             "job.lease_expires_at": {"$gt": now}, "current_version": claim.parent_version},
            {"$set": {"job.lease_expires_at": new_expiry}, "$inc": {"fence": 1}})
        return result.matched_count == 1

    def _validate_coverage(self, candidate: SummaryVersion):
        if len(set(candidate.delta_review_ids)) != len(candidate.delta_review_ids):
            raise ValueError("coverage contains duplicate review IDs")
        if candidate.parent_version is None:
            if (candidate.kind != "initial" or
                    candidate.coverage.historical_sample_count != len(candidate.delta_review_ids) or
                    candidate.coverage.new_review_count != 0):
                raise ValueError("initial coverage does not match sample")
            return
        parent = self.versions.find_one({"product_id": candidate.product_id,
                                         "version": candidate.parent_version})
        if parent is None:
            raise ValueError("coverage parent is missing")
        expected_historical = parent["coverage"]["historical_sample_count"]
        expected_new = parent["coverage"]["new_review_count"] + len(candidate.delta_review_ids)
        if (candidate.kind not in {"reviews", "guidance"} or
                (candidate.kind == "guidance" and candidate.delta_review_ids) or
                candidate.coverage.historical_sample_count != expected_historical or
                candidate.coverage.new_review_count != expected_new):
            raise ValueError("cumulative coverage does not match parent and frozen inputs")

    def freeze(self, claim, review_ids, guidance_ids, *, refresh_ids=None) -> FrozenUpdate:
        state = self._owned(claim)
        if state is None:
            raise ValueError("claim is no longer current")
        job = state["job"]
        if job.get("review_ids") is not None:
            return FrozenUpdate(claim.product_id, claim.job_id, claim.parent_version,
                                job["version"], job["review_ids"], job["guidance_ids"],
                                job.get("refresh_ids", []))
        if len(review_ids) > 20 or len(set(review_ids)) != len(review_ids):
            raise ValueError("review batch must contain at most 20 unique IDs")
        source = "initial" if claim.parent_version is None else "user_submission"
        query = self._pending_query(claim.product_id, boundary=job["boundary"], source=source)
        if review_ids:
            query["review_id"] = {"$in": review_ids}
        selected = list(self.inputs.find(query).sort([("source_timestamp", 1), ("review_id", 1)]).limit(21))
        if review_ids and len(selected) != len(review_ids):
            raise ValueError("review batch includes unavailable inputs")
        selected = selected[:20]
        ids = [item["review_id"] for item in selected]
        if refresh_ids is None:
            refresh_ids = self.pending_refresh_ids(claim)
        else:
            refresh_ids = list(refresh_ids)
            if len(refresh_ids) > 20 or len(refresh_ids) != len(set(refresh_ids)):
                raise ValueError("refresh batch must contain at most 20 unique IDs")
            rows = list(self.inputs.find({**self._refresh_query(claim.product_id, job["boundary"]),
                                          "review_id": {"$in": refresh_ids}})
                        .sort("admission_sequence", 1))
            if [row["review_id"] for row in rows] != refresh_ids:
                raise ValueError("refresh batch includes unavailable or unordered inputs")
        guidance_ids = list(guidance_ids)
        if len(set(guidance_ids)) != len(guidance_ids):
            raise ValueError("guidance IDs must be unique")
        version = state["next_version"]
        frozen = {"job.review_ids": ids, "job.guidance_ids": list(guidance_ids),
                  "job.refresh_ids": refresh_ids, "job.version": version}
        self.capacity.check_documents([{"review_ids": ids, "guidance_ids": guidance_ids,
                                        "refresh_ids": refresh_ids, "version": version}])
        updated = self.states.find_one_and_update(
            {"_id": claim.product_id, "job.owner_token": claim.owner_token,
             "job.job_id": claim.job_id, "job.review_ids": None,
             "current_version": claim.parent_version, "next_version": version},
            {"$set": frozen, "$inc": {"next_version": 1}}, return_document=ReturnDocument.AFTER)
        if updated is None:
            state = self._owned(claim)
            if state and state["job"].get("review_ids") is not None:
                job = state["job"]
                return FrozenUpdate(claim.product_id, claim.job_id, claim.parent_version,
                                    job["version"], job["review_ids"], job["guidance_ids"],
                                    job.get("refresh_ids", []))
            raise ValueError("claim changed during freeze")
        return FrozenUpdate(claim.product_id, claim.job_id, claim.parent_version,
                            version, ids, list(guidance_ids), refresh_ids)

    def complete_noop(self, claim, refresh_ids, now) -> bool:
        """Fence and consume refresh-only work that adds no summary content."""
        state = self._owned(claim, now)
        if (state is None or claim.parent_version is None or not refresh_ids or
                state["job"].get("review_ids") not in (None, []) or
                state["job"].get("guidance_ids") not in (None, []) or
                self.versions.find_one({"product_id": claim.product_id, "job_id": claim.job_id}) or
                self._pending_count(claim.product_id, boundary=state["job"]["boundary"])):
            return False
        if len(refresh_ids) > 20 or len(refresh_ids) != len(set(refresh_ids)):
            raise ValueError("invalid no-op refresh batch")
        rows = list(self.inputs.find({**self._refresh_query(claim.product_id, state["job"]["boundary"]),
                                      "review_id": {"$in": refresh_ids}})
                    .sort("admission_sequence", 1))
        if [row["review_id"] for row in rows] != list(refresh_ids):
            return False
        remaining_refresh = self.inputs.count_documents({
            **self._refresh_query(claim.product_id, state["job"]["boundary"]),
            "review_id": {"$nin": refresh_ids}})
        drain_boundary = state.get("drain_boundary") if remaining_refresh else None
        updated = self.states.update_one({"_id": claim.product_id,
            "current_version": claim.parent_version, "job.owner_token": claim.owner_token,
            "job.job_id": claim.job_id, "job.lease_expires_at": {"$gt": now}},
            {"$set": {"job": None, "status": "ready", "drain_boundary": drain_boundary},
             "$unset": {"error_code": "", "next_attempt_at": ""}})
        if updated.matched_count != 1:
            return False
        self.inputs.update_many({"product_id": claim.product_id,
            "review_id": {"$in": refresh_ids}, "incorporated_version": None},
            {"$set": {"incorporated_version": claim.parent_version}})
        return True

    def stage(self, claim, version) -> str:
        state = self._owned(claim)
        if state is None or state["job"].get("review_ids") is None:
            raise ValueError("claim must be frozen before staging")
        job = state["job"]
        candidate = version if isinstance(version, SummaryVersion) else SummaryVersion.model_validate(version)
        self._validate_coverage(candidate)
        if (candidate.product_id != claim.product_id or candidate.job_id != claim.job_id or
                candidate.parent_version != claim.parent_version or candidate.version != job["version"] or
                candidate.delta_review_ids != job["review_ids"] or
                candidate.guidance_references != job["guidance_ids"] or
                candidate.published_at is not None):
            raise ValueError("candidate does not match frozen claim")
        document = candidate.model_dump(mode="python")
        document["semantic_review"] = candidate.semantic_review.model_dump(exclude_none=True)
        document["refresh_ids"] = job.get("refresh_ids", [])
        document["memory_sync"] = self._memory_sync(claim.product_id, candidate.version)
        document["_id"] = f"{claim.product_id}:{candidate.version}"
        existing = self.versions.find_one({"product_id": claim.product_id, "job_id": claim.job_id})
        if existing:
            if artifact_sha256(existing) != artifact_sha256(candidate):
                raise ValueError("staged job has different artifact")
            return existing["_id"]
        self.capacity.check_documents([document])
        try:
            self.versions.insert_one(document)
        except DuplicateKeyError:
            existing = self.versions.find_one({"product_id": claim.product_id, "job_id": claim.job_id})
            if existing is None or artifact_sha256(existing) != artifact_sha256(candidate):
                raise
            return existing["_id"]
        return document["_id"]

    def staged(self, claim) -> SummaryVersion | None:
        state = self._owned(claim)
        if state is None:
            return None
        document = self.versions.find_one({"product_id": claim.product_id, "job_id": claim.job_id})
        return _public_version(document) if document else None

    def draft(self, version_id) -> SummaryVersion | None:
        document = self.versions.find_one({"_id": version_id})
        return _public_version(document) if document else None

    def draft_metadata(self, version_id) -> dict | None:
        document = self.versions.find_one({"_id": version_id},
                                          {"raw_artifact_sha256": 1, "model_manifest": 1})
        if document is None:
            return None
        return {key: document[key] for key in ("raw_artifact_sha256", "model_manifest")
                if key in document}

    def stage_initial_draft(self, product_id, candidate, raw_artifact_bytes, model_manifest) -> str:
        """Persist one distinct pilot candidate before semantic review.

        The supplied version is a placeholder; this method allocates the final
        number atomically. The stable job_id identifies retries of one draft.
        """
        version = candidate if isinstance(candidate, SummaryVersion) else SummaryVersion.model_validate(candidate)
        if (version.product_id != product_id or version.kind != "initial" or
                version.parent_version is not None or version.published_at is not None or
                version.semantic_review.status != "pending" or len(version.delta_review_ids) > 20):
            raise ValueError("invalid initial draft")
        self._validate_coverage(version)
        if version.delta_review_ids:
            admitted = self.inputs.count_documents({"product_id": product_id, "source": "initial",
                "review_id": {"$in": version.delta_review_ids}})
            if admitted != len(version.delta_review_ids):
                raise ValueError("initial sample has unadmitted review IDs")
        if not isinstance(raw_artifact_bytes, bytes) or not isinstance(model_manifest, dict):
            raise ValueError("raw artifact bytes and model manifest required")
        generated = GeneratedSummary.model_validate_json(raw_artifact_bytes)
        if (generated.narrative != version.narrative or generated.themes != version.themes or
                generated.contradictions != version.contradictions):
            raise ValueError("raw artifact does not match staged content")
        raw_digest = hashlib.sha256(raw_artifact_bytes).hexdigest()
        existing = self.versions.find_one({"product_id": product_id, "job_id": version.job_id})
        if existing:
            if (existing.get("raw_artifact_sha256") != raw_digest or
                    existing.get("model_manifest") != model_manifest or
                    existing["narrative"] != version.narrative or
                    existing["delta_review_ids"] != version.delta_review_ids):
                raise ValueError("pilot job changed on retry")
            return existing["_id"]
        self._state(product_id)
        self.capacity.check_documents([{"pilot": version.model_dump(mode="python"),
                                        "raw_artifact_sha256": raw_digest,
                                        "model_manifest": model_manifest}])
        state = self.states.find_one_and_update({"_id": product_id, "current_version": None},
            {"$inc": {"next_version": 1}}, return_document=ReturnDocument.BEFORE)
        if state is None:
            raise ValueError("initialization already published")
        allocated = version.model_copy(update={"version": state["next_version"]})
        document = allocated.model_dump(mode="python")
        document["_id"] = f"{product_id}:{allocated.version}"
        document["semantic_review"] = {"status": "pending"}
        document["raw_artifact_sha256"] = raw_digest
        document["model_manifest"] = model_manifest
        document["memory_sync"] = self._memory_sync(product_id, allocated.version)
        self.capacity.check_documents([document])
        try:
            self.versions.insert_one(document)
        except DuplicateKeyError:
            existing = self.versions.find_one({"product_id": product_id, "job_id": version.job_id})
            if existing is None or existing.get("raw_artifact_sha256") != raw_digest:
                raise
            return existing["_id"]
        return document["_id"]

    def claim_initial_candidate(self, product_id, version_id, now, lease_seconds) -> SummaryClaim | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        candidate = self.versions.find_one({"_id": version_id, "product_id": product_id,
                                            "parent_version": None, "kind": "initial"})
        if candidate is None:
            return None
        state = self._state(product_id)
        if state.get("current_version") is not None:
            return None
        old_job = state.get("job")
        if old_job and old_job["lease_expires_at"] > now:
            return None
        job = {"job_id": candidate["job_id"], "parent_version": None,
               "boundary": state.get("admission_sequence", 0),
               "review_ids": candidate["delta_review_ids"],
               "guidance_ids": candidate["guidance_references"],
               "refresh_ids": [], "version": candidate["version"],
               "owner_token": uuid4().hex, "lease_seconds": lease_seconds,
               "lease_expires_at": now + timedelta(seconds=lease_seconds)}
        updated = self.states.find_one_and_update(
            {"_id": product_id, "fence": state["fence"], "current_version": None},
            {"$set": {"job": job, "status": "updating"}, "$inc": {"fence": 1}},
            return_document=ReturnDocument.AFTER)
        return self._claim_from(updated) if updated else None

    def apply_semantic_review(self, product_id, version_id, review) -> bool:
        decision = review if isinstance(review, SemanticReview) else SemanticReview.model_validate(review)
        if decision.status == "pending":
            raise ValueError("review decision must be completed")
        document = self.versions.find_one({"_id": version_id, "product_id": product_id})
        if document is None or document.get("published_at") is not None:
            return False
        if decision.artifact_sha256 != _review_digest(document):
            raise ValueError("semantic review artifact digest does not match staged artifact")
        result = self.versions.update_one(
            {"_id": version_id, "product_id": product_id, "published_at": None,
             "semantic_review.status": "pending"},
            {"$set": {"semantic_review": decision.model_dump(exclude_none=True)}})
        return result.matched_count == 1

    def publish(self, claim, version_id, now) -> bool:
        state = self._owned(claim, now)
        if state is None:
            return False
        job = state["job"]
        candidate = self.versions.find_one({"_id": version_id, "product_id": claim.product_id,
                                            "job_id": claim.job_id, "version": job["version"]})
        if (candidate is None or candidate["parent_version"] != claim.parent_version or
                candidate["delta_review_ids"] != job["review_ids"] or
                candidate["guidance_references"] != job["guidance_ids"]):
            return False
        try:
            self._validate_coverage(_public_version(candidate))
        except ValueError:
            return False
        if candidate["kind"] == "initial":
            review = candidate.get("semantic_review", {})
            if (review.get("status") != "approved" or
                    review.get("artifact_sha256") != _review_digest(candidate) or
                    not all(review.get(field) is True for field in
                            ("factual_support", "coverage", "classification"))):
                return False
        updated = self.states.update_one(
            {"_id": claim.product_id, "current_version": claim.parent_version,
             "job.owner_token": claim.owner_token, "job.job_id": claim.job_id,
             "job.lease_expires_at": {"$gt": now}},
            {"$set": {"current_version": candidate["version"], "last_updated_at": now,
                      "status": "ready"},
             "$unset": {"error_code": "", "next_attempt_at": ""}})
        if updated.matched_count != 1:
            return False
        self.reconcile(claim.product_id)
        return True

    def _chain(self, product_id):
        state = self.states.find_one({"_id": product_id})
        number = state.get("current_version") if state else None
        chain = []
        seen = set()
        while number is not None:
            if number in seen:
                raise ValueError("cyclic published chain")
            seen.add(number)
            document = self.versions.find_one({"product_id": product_id, "version": number})
            if document is None:
                raise ValueError("missing published version")
            chain.append(document)
            number = document["parent_version"]
        return state, chain

    def reconcile(self, product_id) -> None:
        state, chain = self._chain(product_id)
        if state is None:
            return
        reachable = {document["version"] for document in chain}
        for document in reversed(chain):
            number = document["version"]
            if document.get("published_at") is None:
                published_at = (state.get("last_updated_at") if number == state.get("current_version")
                                else document["created_at"])
                self.versions.update_one({"_id": document["_id"], "published_at": None},
                                         {"$set": {"published_at": published_at}})
            if "memory_sync" not in document:
                self.versions.update_one({"_id": document["_id"], "memory_sync": {"$exists": False}},
                                         {"$set": {"memory_sync": self._memory_sync(product_id, number)}})
            if document["delta_review_ids"]:
                self.inputs.update_many({"product_id": product_id,
                                         "review_id": {"$in": document["delta_review_ids"]}},
                                        {"$set": {"incorporated_version": number}})
            refresh_ids = document.get("refresh_ids", [])
            if refresh_ids:
                self.inputs.update_many({"product_id": product_id, "review_id": {"$in": refresh_ids}},
                                        {"$set": {"incorporated_version": number}})
        # An orphan's ledger mark cannot hide an unincorporated review.
        self.inputs.update_many({"product_id": product_id,
                                 "incorporated_version": {"$nin": [None, *reachable]}},
                                {"$set": {"incorporated_version": None}})
        job = state.get("job")
        if job and state.get("current_version") != job.get("parent_version"):
            boundary = state.get("drain_boundary")
            if boundary is not None and self._eligible_count(product_id, boundary=boundary) == 0:
                boundary = None
            self.states.update_one({"_id": product_id, "current_version": state["current_version"],
                                    "job.job_id": job["job_id"]},
                                   {"$set": {"job": None, "drain_boundary": boundary}})

    def current(self, product_id) -> SummaryView:
        self.reconcile(product_id)
        state = self.states.find_one({"_id": product_id})
        if state is None:
            return SummaryView(product_id=product_id, current=None, last_updated_at=None,
                               update_threshold=1, pending_review_count=0,
                               status="uninitialized", error_code=None, memory_status="unknown")
        document = self.versions.find_one({"product_id": product_id,
                                           "version": state["current_version"]}) if state.get("current_version") else None
        version = _public_version(document) if document else None
        initial_candidate = None
        if version is None:
            draft = self.versions.find_one({"product_id": product_id, "kind": "initial",
                "parent_version": None, "published_at": None,
                "semantic_review.status": {"$in": ["pending", "approved"]}},
                sort=[("version", -1)])
            initial_candidate = _public_version(draft) if draft else None
        pending = self._pending_count(product_id)
        status = state.get("status") or ("uninitialized" if version is None else "ready")
        if state.get("job") is None and status != "failed":
            if version is None:
                status = "uninitialized"
            elif pending >= state.get("update_threshold", 1) or self._refresh_count(product_id):
                status = "queued"
            else:
                status = "waiting" if pending else "ready"
        return SummaryView(product_id=product_id, current=version,
                           initial_candidate=initial_candidate,
                           last_updated_at=version.published_at if version else None,
                           update_threshold=state.get("update_threshold", 1),
                           pending_review_count=pending, status=status,
                           error_code=state.get("error_code"),
                           memory_status=(document.get("memory_sync", {}).get("status", "pending")
                                          if document else state.get("memory_status", "unknown")))

    def claim_memory_sync(self, now, lease_seconds) -> MemorySyncClaim | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        # A published_at field alone cannot authorize an orphan. Only the
        # state's immutable parent chain may enter the outbox.
        for state in self.states.find({"current_version": {"$ne": None}}, {"_id": 1}).sort("_id", 1):
            self.reconcile(state["_id"])
            _, chain = self._chain(state["_id"])
            for document in reversed(chain):
                sync = document.get("memory_sync")
                if not sync or sync.get("status") not in {"pending", "failed", "updating"}:
                    continue
                if (sync.get("next_attempt_at") and sync["next_attempt_at"] > now) or (
                        sync.get("lease_expires_at") and sync["lease_expires_at"] > now):
                    continue
                owner = uuid4().hex
                expiry = now + timedelta(seconds=lease_seconds)
                updated = self.versions.find_one_and_update(
                    {"_id": document["_id"], "memory_sync.status": sync["status"],
                     "memory_sync.owner_token": sync.get("owner_token"),
                     "memory_sync.lease_expires_at": sync.get("lease_expires_at")},
                    {"$set": {"memory_sync.status": "updating", "memory_sync.owner_token": owner,
                              "memory_sync.lease_expires_at": expiry},
                     "$inc": {"memory_sync.attempts": 1}}, return_document=ReturnDocument.AFTER)
                if updated:
                    return MemorySyncClaim(updated["product_id"], updated["version"], owner,
                                           sync["idempotency_key"], expiry)
        return None

    def memory_sync_checkpoint(self, claim, data) -> None:
        if not isinstance(data, dict):
            raise ValueError("checkpoint must be an object")
        self.capacity.check_documents([{"checkpoint": data}])
        updated = self.versions.update_one(
            {"product_id": claim.product_id, "version": claim.version,
             "memory_sync.owner_token": claim.owner_token, "memory_sync.status": "updating"},
            {"$set": {"memory_sync.checkpoint": data}})
        if updated.matched_count != 1:
            raise ValueError("memory sync claim changed")

    def get_memory_sync_checkpoint(self, claim) -> dict | None:
        document = self.versions.find_one(
            {"product_id": claim.product_id, "version": claim.version,
             "memory_sync.owner_token": claim.owner_token, "memory_sync.status": "updating"},
            {"memory_sync.checkpoint": 1})
        return document.get("memory_sync", {}).get("checkpoint") if document else None

    def complete_memory_sync(self, claim, now) -> bool:
        updated = self.versions.update_one(
            {"product_id": claim.product_id, "version": claim.version,
             "memory_sync.owner_token": claim.owner_token,
             "memory_sync.status": "updating", "memory_sync.lease_expires_at": {"$gt": now}},
            {"$set": {"memory_sync.status": "synced", "memory_sync.synced_at": now,
                      "memory_sync.owner_token": None, "memory_sync.lease_expires_at": None,
                      "memory_sync.error_code": None}})
        return updated.matched_count == 1

    def fail_memory_sync(self, claim, code, now) -> bool:
        if not isinstance(code, str) or not code:
            raise ValueError("error code required")
        updated = self.versions.update_one(
            {"product_id": claim.product_id, "version": claim.version,
             "memory_sync.owner_token": claim.owner_token, "memory_sync.status": "updating"},
            {"$set": {"memory_sync.status": "failed", "memory_sync.error_code": code,
                      "memory_sync.next_attempt_at": now + timedelta(seconds=30),
                      "memory_sync.owner_token": None, "memory_sync.lease_expires_at": None}})
        return updated.matched_count == 1

    def history(self, product_id, cursor, limit) -> HistoryPage:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be 1..100")
        self.reconcile(product_id)
        _, chain = self._chain(product_id)
        if cursor is not None:
            try:
                position = next(i for i, item in enumerate(chain) if item["version"] == int(cursor)) + 1
            except (ValueError, StopIteration):
                raise ValueError("invalid history cursor") from None
            chain = chain[position:]
        items = chain[:limit]
        next_cursor = str(items[-1]["version"]) if len(chain) > limit else None
        return HistoryPage(items=[_public_version(item) for item in items],
                           next_cursor=next_cursor)

    def version(self, product_id, number) -> SummaryVersion | None:
        self.reconcile(product_id)
        _, chain = self._chain(product_id)
        for document in chain:
            if document["version"] == number:
                return _public_version(document)
        return None
