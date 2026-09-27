"""Dedicated incremental summary worker with fenced publication and retry state."""

from datetime import datetime, timezone
from threading import Event, Thread
import json

from app.domain import MemoryRecord
from app.errors import ServiceError
from app.integrations.memory import ProviderError
from app.summaries.contracts import SummaryCoverage, SummaryVersion
from app.summaries.generation import PROMPT_VERSION, SummaryGenerationError, SummaryGenerator


def utcnow():
    return datetime.now(timezone.utc)


class SummaryWorker:
    def __init__(self, database, repository, provider, *, memory=None, lease_seconds=180, clock=utcnow):
        self.database = database
        self.repository = repository
        self.provider = provider
        self.memory = memory
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.stopped = Event()

    def stop(self):
        self.stopped.set()

    def reconcile_submissions(self, limit=100):
        """Admit acknowledged live reviews missed by a save-before-ledger crash.

        Missing markers cover pre-cutover user submissions once. Imported reviews
        never match this query.
        """
        query = {"source": "user_submission", "$or": [
            {"summary_input_outstanding": True}, {"summary_input_outstanding": {"$exists": False}}]}
        count = 0
        for review in self.database.reviews.find(query).sort("_id", 1).limit(limit):
            self.repository.admit_review(review)
            self.database.reviews.update_one({"_id": review["_id"]},
                                             {"$set": {"summary_input_outstanding": False}})
            count += 1
        return count

    def reconcile_guidance(self, limit=100):
        query = {"$or": [{"summary_refresh_outstanding": True},
                         {"summary_refresh_outstanding": {"$exists": False}}]}
        count = 0
        for decision in self.database.decisions.find(query).sort("_id", 1).limit(limit):
            self.repository.request_refresh(decision["parent_asin"],
                                            "decision:" + str(decision["_id"]), "guidance")
            self.database.decisions.update_one({"_id": decision["_id"]},
                                               {"$set": {"summary_refresh_outstanding": False}})
            count += 1
        return count

    def _guidance(self, claim, prospective_review_ids):
        """Freeze only decisions admitted by claim time and grounded in coverage."""
        product_id = claim.product_id
        state = self.repository.states.find_one({"_id": product_id,
            "job.owner_token": claim.owner_token}, {"job.boundary": 1})
        if not state:
            raise SummaryGenerationError("summary_claim_lost")
        boundary = state["job"]["boundary"]
        # Reconciliation removes orphan ledger marks before selecting eligible
        # evidence. Newly frozen review IDs are the only unpublished additions.
        self.repository.reconcile(product_id)
        allowed = {row["review_id"] for row in self.repository.inputs.find({
            "product_id": product_id, "source": {"$in": ["initial", "user_submission"]},
            "incorporated_version": {"$ne": None}}, {"review_id": 1})}
        allowed.update(prospective_review_ids)
        markers = self.repository.inputs.find({"product_id": product_id,
            "source": "refresh_guidance", "admission_sequence": {"$lte": boundary}},
            {"review_id": 1}).sort("admission_sequence", -1).limit(100)
        prefix = "_refresh:decision:"
        ids = [row["review_id"][len(prefix):] for row in markers
               if row["review_id"].startswith(prefix)]
        rows = {str(row["_id"]): row for row in self.database.decisions.find({
            "_id": {"$in": ids}, "parent_asin": product_id})}
        selected = []
        for decision_id in reversed(ids):
            row = rows.get(decision_id)
            if row is None or not set(row.get("evidence_ids", [])) <= allowed:
                continue
            selected.append({"id": decision_id, "kind": row["kind"],
                             "rationale": row["rationale"]})
        return selected

    def _heartbeat(self, claim, done, lost):
        while not done.wait(max(1, self.lease_seconds / 3)):
            try:
                if not self.repository.renew(claim, self.clock()):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    def sync_memory_once(self) -> bool:
        claim = self.repository.claim_memory_sync(self.clock(), self.lease_seconds)
        if claim is None:
            return False
        try:
            version = self.repository.version(claim.product_id, claim.version)
            if version is None:
                raise ProviderError("summary_version_unavailable")
            if self.memory is None:
                raise ProviderError("memory_not_configured")
            content = json.dumps({"product_id": claim.product_id, "version": claim.version,
                "narrative": version.narrative,
                "themes": [theme.model_dump(mode="json") for theme in version.themes],
                "contradictions": version.contradictions,
                "coverage": version.coverage.model_dump()},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record = MemoryRecord(id=claim.idempotency_key, content=content,
                occurred_at=version.published_at, metadata={"parent_asin": claim.product_id,
                    "version": claim.version, "kind": "product_summary"})
            self.memory.ensure_retained("summary-" + claim.product_id, record,
                state=self.repository.get_memory_sync_checkpoint(claim),
                checkpoint=lambda data: self.repository.memory_sync_checkpoint(claim, data))
            self.repository.complete_memory_sync(claim, self.clock())
        except (ProviderError, ServiceError, ValueError) as exc:
            self.repository.fail_memory_sync(claim, getattr(exc, "code", "summary_memory_failed"), self.clock())
        return True

    def tick(self) -> bool:
        if self.stopped.is_set():
            return False
        self.reconcile_submissions()
        self.reconcile_guidance()
        # Memory retention is an independent outbox. Its failure cannot undo a
        # committed Mongo publication or block the next incremental claim.
        synced = self.sync_memory_once()
        claim = self.repository.next_claim(self.clock(), self.lease_seconds)
        if claim is None:
            return synced
        done, lost = Event(), Event()
        heartbeat = Thread(target=self._heartbeat, args=(claim, done, lost), daemon=True)
        heartbeat.start()
        try:
            staged = self.repository.staged(claim)
            if staged is not None:
                version_id = f"{claim.product_id}:{staged.version}"
            else:
                product = self.database.products.find_one({"_id": claim.product_id})
                parent = self.repository.version(claim.product_id, claim.parent_version)
                if product is None or parent is None:
                    raise SummaryGenerationError("summary_parent_missing")
                prospective_reviews = self.repository.pending_rows(claim)
                candidate_guidance = self._guidance(claim,
                    {str(row["_id"]) for row in prospective_reviews})
                frozen = self.repository.freeze(claim, [], [item["id"] for item in candidate_guidance])
                frozen_guidance = {item["id"]: item for item in candidate_guidance}
                missing_guidance = [item_id for item_id in frozen.guidance_ids if item_id not in frozen_guidance]
                if missing_guidance:
                    rows = self.database.decisions.find({"_id": {"$in": missing_guidance},
                                                          "parent_asin": claim.product_id})
                    frozen_guidance.update({str(row["_id"]): {"id": str(row["_id"]),
                        "kind": row["kind"], "rationale": row["rationale"]} for row in rows})
                if any(item_id not in frozen_guidance for item_id in frozen.guidance_ids):
                    raise SummaryGenerationError("summary_guidance_missing")
                guidance = [frozen_guidance[item_id] for item_id in frozen.guidance_ids]
                reviews = self.repository.pending_rows(claim)
                if [str(row["_id"]) for row in reviews] != frozen.review_ids:
                    raise SummaryGenerationError("summary_input_missing")
                if self.provider is None:
                    raise SummaryGenerationError("provider_not_configured")
                def old_evidence_lookup(product_id, ids):
                    return {str(row["_id"]): row for row in self.database.reviews.find(
                        {"_id": {"$in": ids}, "parent_asin": product_id})}
                generator = SummaryGenerator(self.provider, old_evidence_lookup,
                    max_prompt_bytes=getattr(self.provider, "summary_prompt_budget_bytes", 6000),
                    save_checkpoint=lambda value: self.repository.checkpoint(claim, value))
                generated = generator.generate(product, parent, reviews, guidance,
                                               checkpoint=self.repository.get_checkpoint(claim))
                version = SummaryVersion(
                    **generated.model_dump(), product_id=claim.product_id,
                    version=frozen.version, parent_version=claim.parent_version,
                    job_id=claim.job_id, kind="reviews" if frozen.review_ids else "guidance",
                    coverage=SummaryCoverage(
                        historical_sample_count=parent.coverage.historical_sample_count,
                        new_review_count=parent.coverage.new_review_count + len(frozen.review_ids)),
                    delta_review_ids=frozen.review_ids, manifest_ref=parent.manifest_ref,
                    model_identity=self.provider.model, prompt_version=PROMPT_VERSION,
                    guidance_references=frozen.guidance_ids, created_at=self.clock())
                version_id = self.repository.stage(claim, version)
            if lost.is_set() or self.stopped.is_set():
                return True
            if not self.repository.publish(claim, version_id, self.clock()):
                raise SummaryGenerationError("summary_publication_conflict")
        except (SummaryGenerationError, ProviderError, ServiceError, ValueError) as exc:
            self.repository.fail(claim, getattr(exc, "code", "summary_generation_failed"), self.clock())
        finally:
            done.set()
            heartbeat.join(timeout=1)
        return True
