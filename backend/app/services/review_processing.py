"""Incremental, provisional interpretation in an isolated arrival-time memory bank."""
from threading import Event
from app.domain import MemoryRecord, Scope
from app.errors import ServiceError
from app.integrations.memory import ProviderError, RetentionPending
from app.repositories.jobs import JobRepository
from app.repositories.reviews import ReviewRepository
from app.services.decisions import DecisionService, utcnow
from app.services.evidence import validate_findings
from app.worker import JobContext, JobResult


def save_checkpoint(context, stage, value):
    if not context.checkpoint(stage, value): raise ProviderError('lease_lost')


def stage_status(context, stage, status):
    if context.cancelled.is_set() or context.shutdown.is_set() or not context.repository.stage_status(context.claim, stage, status, context.clock()):
        raise ProviderError('lease_lost')


class ReviewProcessor:
    def __init__(self, database, model, memory, *, clock=utcnow, capacity=None):
        self.repository = ReviewRepository(database, capacity=capacity)
        self.jobs = JobRepository(database, capacity=capacity)
        self.decisions = DecisionService(database, memory, clock=clock, capacity=capacity)
        self.model, self.memory, self.clock = model, memory, clock

    def handle(self, review, context):
        if self.memory is None: raise ServiceError('provider_not_configured', 503)
        checkpoints = review.get('processing', {}).get('checkpoints', {})
        frozen = checkpoints.get('classification')
        if frozen is None:
            cutoff = review.get('created_at', review['timestamp'])
            scope = Scope(source=review.get('source', 'amazon_2023'), batch_id=review.get('batch_id'), evaluation=bool(review.get('held_out') or review.get('batch') == 'C'))
            guidance = self.decisions.eligible(review['parent_asin'], scope, cutoff)
            frozen = {'decision_ids': [d['_id'] for d in guidance]}
            save_checkpoint(context, 'classification', frozen)
        # Decision payloads are immutable through the supported API.
        # Preserve existing small embedded checkpoints on retry.
        guidance = (self.decisions.repository.by_ids(review['parent_asin'], frozen['decision_ids'])
                    if 'decision_ids' in frozen else frozen['decisions'])
        records = [MemoryRecord(id='review:'+review['_id'], content=review['text'], occurred_at=review['timestamp'],
                                metadata={'parent_asin':review['parent_asin'], 'source':review.get('source','amazon_2023'), 'kind':'review'})]
        records += [MemoryRecord(id='decision:'+d['_id'], content=d['rationale'], occurred_at=d['decided_at'],
                                 metadata={'parent_asin':review['parent_asin'], 'kind':d['kind']}) for d in guidance]
        bank = 'review-' + review['_id']
        state = checkpoints.get('memory', {'next_index':0, 'current':None})
        position = state['next_index']
        for index in range(position, min(position+20, len(records))):
            try:
                self.memory.ensure_retained(bank, records[index], state=state.get('current') if index == position else None,
                    checkpoint=lambda value, index=index: save_checkpoint(context, 'memory', {'next_index':index, 'current':value}))
            except RetentionPending:
                return JobResult()
            save_checkpoint(context, 'memory', {'next_index':index+1, 'current':None})
        if position+20 < len(records): return JobResult()
        stage_status(context, 'memory', 'synced')
        recalled = self.memory.recall(bank, 'Applicable PM guidance for this product review')
        if not set(recalled.record_ids) <= {r.id for r in records}: raise ProviderError('memory_scope_violation')
        if self.model is None: raise ServiceError('provider_not_configured', 503)
        drafts = self.model.extract(self.repository.product(review['parent_asin']), [review], recalled)
        findings = validate_findings(drafts, [review])
        if len(findings) > 100: raise ServiceError('classification_output_limit_exceeded', 422)
        return JobResult(completed=True, updates={'provisional_findings':findings,
            'guidance_references':[d['_id'] for d in guidance if 'decision:'+d['_id'] in recalled.record_ids]})

    def process(self, claim):
        context = JobContext(self.jobs, claim, self.clock, Event(), Event())
        result = self.handle(self.jobs.get(claim), context)
        if result.completed: self.jobs.finish(claim, result.updates, self.clock())
        else: self.jobs.defer(claim, self.clock())
