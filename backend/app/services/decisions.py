"""Real PM knowledge always keeps its server-observed creation time."""
from datetime import datetime, timezone
from app.errors import ServiceError
from app.repositories.decisions import DecisionRepository
from app.repositories.analysis import AnalysisRepository


def utcnow(): return datetime.now(timezone.utc)


def public_decision(row):
    return {'id': row['_id'], **{key: row[key] for key in ('parent_asin', 'kind', 'rationale', 'evidence_ids', 'decided_at', 'available_through', 'processing')}}


class DecisionService:
    def __init__(self, database, memory=None, *, clock=utcnow, capacity=None):
        self.repository = DecisionRepository(database, capacity=capacity)
        self.analysis = AnalysisRepository(database, capacity=capacity)
        self.memory, self.clock = memory, clock

    def create(self, product_id, principal, payload):
        if principal.role != 'pm': raise ServiceError('forbidden', 403)
        if self.repository.product(product_id) is None: raise ServiceError('product_not_found', 404)
        if payload.kind not in {'correction', 'preference', 'decision'} or not payload.rationale.strip() or len(payload.rationale) > 10000 or len(payload.evidence_ids) > 100:
            raise ServiceError('invalid_decision', 422)
        now = self.clock()
        cutoff = payload.available_through or now
        if cutoff > now: raise ServiceError('future_cutoff', 422)
        evidence = self.repository.evidence(payload.evidence_ids)
        if {r['_id'] for r in evidence} != set(payload.evidence_ids) or any(
            r['parent_asin'] != product_id or any(r.get(key, r['timestamp']) > cutoff for key in ('timestamp', 'created_at', 'available_at')) for r in evidence):
            raise ServiceError('invalid_decision_evidence', 422)
        return public_decision(self.repository.create(product_id, principal, payload, now))

    def eligible(self, product_id, scope, snapshot_at):
        cutoff = min(snapshot_at, scope.available_through) if scope.available_through else snapshot_at
        eligible, after = [], None
        while True:
            decisions = self.repository.guidance_candidates(product_id, cutoff, after=after)
            if not decisions: return eligible
            if any(len(d.get('evidence_ids', [])) > 100 for d in decisions):
                raise ServiceError('guidance_evidence_limit_exceeded', 422)
            referenced_ids = sorted({key for d in decisions for key in d.get('evidence_ids', [])})
            reviews = self.analysis.reviews(product_id, scope.model_dump(), cutoff, len(referenced_ids),
                                            review_ids=referenced_ids) if referenced_ids else []
            eligible_ids = {r['_id'] for r in reviews}
            for decision in decisions:
                if set(decision.get('evidence_ids', [])) <= eligible_ids:
                    eligible.append(decision)
                    if len(eligible) > 100: raise ServiceError('narrower_guidance_scope_required', 422)
            after = (decisions[-1]['decided_at'], decisions[-1]['_id'])

    def list(self, product_id, principal, limit=20):
        if principal.role != 'pm': raise ServiceError('forbidden', 403)
        if not 1 <= limit <= 100: raise ServiceError('invalid_page_size', 422)
        if self.repository.product(product_id) is None: raise ServiceError('product_not_found', 404)
        return {'items': [public_decision(row) for row in self.repository.list(product_id, limit)]}

    def handle(self, decision, context):
        from app.domain import MemoryRecord
        from app.integrations.memory import RetentionPending
        from app.services.review_processing import save_checkpoint, stage_status
        from app.worker import JobResult
        if self.memory is None: raise ServiceError('provider_not_configured', 503)
        record = MemoryRecord(id='decision:'+decision['_id'], content=decision['rationale'], occurred_at=decision['decided_at'],
                              metadata={'parent_asin':decision['parent_asin'], 'kind':decision['kind']})
        state = decision.get('processing', {}).get('checkpoints', {}).get('memory')
        try:
            self.memory.ensure_retained('decision-'+decision['_id'], record, state=state,
                                       checkpoint=lambda value: save_checkpoint(context, 'memory', value))
        except RetentionPending:
            return JobResult()
        stage_status(context, 'memory', 'synced')
        return JobResult(completed=True)

    def process(self, claim):
        from threading import Event
        from app.repositories.jobs import JobRepository
        from app.worker import JobContext
        jobs = JobRepository(self.repository.database, capacity=self.repository.capacity)
        context = JobContext(jobs, claim, self.clock, Event(), Event())
        result = self.handle(jobs.get(claim), context)
        if result.completed: jobs.finish(claim, result.updates, self.clock())
        else: jobs.defer(claim, self.clock())
