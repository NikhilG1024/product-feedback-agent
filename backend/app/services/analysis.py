"""Frozen-scope analysis; model interpretations never determine the denominators."""
from datetime import datetime, timezone
from threading import Event
from uuid import NAMESPACE_URL, uuid5
from app.domain import FindingDraft, Evidence, MemoryContext, MemoryRecord
from app.errors import ServiceError, safe_processing_error
from app.integrations.memory import ProviderError, RetentionPending
from app.repositories.analysis import AnalysisRepository
from app.repositories.jobs import JobRepository
from app.services.evidence import validate_findings
from app.worker import JobContext, JobResult


def utcnow(): return datetime.now(timezone.utc)


class AnalysisService:
    def __init__(self, database, model, memory, *, clock=utcnow, max_reviews=1500, max_chunk_reviews=5, max_chunk_chars=3000, max_findings=100, capacity=None):
        self.repository = AnalysisRepository(database, capacity=capacity)
        self.jobs = JobRepository(database, capacity=capacity)
        self.model, self.memory, self.clock = model, memory, clock
        if not 1 <= max_findings <= 1000: raise ValueError('Invalid finding limit')
        self.max_findings = max_findings
        self.max_reviews = min(max_reviews, 1500)
        self.max_chunk_reviews = min(max_chunk_reviews, 20)
        self.max_chunk_chars = min(max_chunk_chars, 40000)
        if min(self.max_reviews, self.max_chunk_reviews, self.max_chunk_chars) < 1: raise ValueError('Invalid analysis budget')

    def enqueue(self, product_id, principal, request):
        if principal.role != 'pm': raise ServiceError('forbidden', 403)
        if request.mode not in {'baseline', 'memory'} or request.scope.source not in {'amazon_2023', 'user_submission'}:
            raise ServiceError('invalid_scope', 422)
        if self.model is None or (request.mode == 'memory' and self.memory is None):
            raise ServiceError('provider_not_configured', 503)
        scope = request.scope.model_dump()
        if scope['source'] == 'amazon_2023' and not scope['batch_id']: raise ServiceError('historical_batch_required', 422)
        if scope['source'] == 'user_submission' and scope['batch_id']: raise ServiceError('invalid_scope', 422)
        if scope['batch_id']:
            batch = self.repository.batch(scope['batch_id'])
            if batch is None: raise ServiceError('batch_not_found', 404)
            if (batch.get('held_out') or batch.get('label') == 'C') and not scope['evaluation']:
                raise ServiceError('evaluation_scope_required', 422)
        product = self.repository.product(product_id)
        if product is None: raise ServiceError('product_not_found', 404)
        now = self.clock()
        cutoff = scope['available_through'] or (batch['end_at'] if scope['source'] == 'amazon_2023' else now)
        if scope['source'] == 'amazon_2023':
            if cutoff > batch['end_at']: raise ServiceError('cutoff_after_batch_end', 422)
            scope['available_through'] = cutoff
        if cutoff > now: raise ServiceError('future_cutoff', 422)
        reviews = self.repository.reviews(product_id, scope, cutoff, self.max_reviews + 1)
        if len(reviews) > self.max_reviews: raise ServiceError('narrower_scope_required', 422)
        if not reviews: raise ServiceError('empty_scope', 422)
        self.chunks(reviews)  # reject oversized individual records before creating work
        decisions = self.repository.eligible_decisions(product_id, cutoff, [r['_id'] for r in reviews]) if request.mode == 'memory' else []
        if len(decisions) > 100: raise ServiceError('narrower_guidance_scope_required', 422)
        if any(len(d['rationale']) > 10000 for d in decisions): raise ServiceError('guidance_too_large', 422)
        run = self.repository.create(product, scope, request.mode, reviews, decisions, now, cutoff)
        return self.get(run['_id'])

    def chunks(self, reviews):
        chunks, current, size = [], [], 0
        for review in reviews:
            n = len(review['text'])
            if n > self.max_chunk_chars: raise ServiceError('review_exceeds_model_budget', 422)
            if current and (len(current) >= self.max_chunk_reviews or size+n > self.max_chunk_chars):
                chunks.append(current); current, size = [], 0
            current.append(review); size += n
        if current: chunks.append(current)
        return chunks

    def handle(self, run, context):
        reviews = self.repository.snapshot(run['_id'], 'review')
        recalled = MemoryContext(text='', record_ids=[])
        guidance = []
        if run['mode'] == 'memory':
            decisions = self.repository.snapshot(run['_id'], 'decision')
            records = [MemoryRecord(id='review:'+r['_id'], content=r['text'], occurred_at=r['timestamp'],
                       metadata={'parent_asin':run['parent_asin'], 'source':r.get('source','amazon_2023'), 'kind':'review'}) for r in reviews]
            records += [MemoryRecord(id='decision:'+d['_id'], content=d['rationale'], occurred_at=d['decided_at'],
                        metadata={'parent_asin':run['parent_asin'], 'kind':d['kind']}) for d in decisions]
            bank = 'analysis-' + run['_id']
            state = run.get('processing', {}).get('checkpoints', {}).get('memory', {'next_index':0, 'current':None})
            position = state['next_index']
            def save(value):
                if not context.checkpoint('memory', value): raise ProviderError('lease_lost')
            # A durable completed prefix keeps progress bounded even for 1,500 records.
            for index in range(position, min(position+20, len(records))):
                def checkpoint(value, index=index):
                    save({'next_index':index, 'current':value})
                try:
                    self.memory.ensure_retained(bank, records[index], state=state.get('current') if index==position else None,
                                                checkpoint=checkpoint)
                except RetentionPending:
                    return JobResult()
                save({'next_index':index+1, 'current':None})
            if position+20 < len(records): return JobResult()
            recalled = self.memory.recall(bank, 'Summarize product feedback and applicable PM guidance for ' + str(run['product']['title'])[:300])
            allowed = {record.id for record in records}
            if not set(recalled.record_ids) <= allowed: raise ProviderError('memory_scope_violation')
            guidance = [{'decision_id':d['_id'], 'kind':d['kind'], 'rationale':d['rationale']}
                        for d in decisions if 'decision:'+d['_id'] in recalled.record_ids]
        drafts = []
        chunks = self.chunks(reviews)
        checkpoint = run.get('processing', {}).get('checkpoints', {}).get('analysis', {})
        completed = list(checkpoint.get('chunk_results', []))
        if len(completed) > len(chunks):
            raise ProviderError('analysis_checkpoint_unavailable')
        for index, identifier in enumerate(completed):
            saved = self.repository.extraction(run['_id'], identifier)
            if saved is None or saved['review_ids'] != [r['_id'] for r in chunks[index]]:
                raise ProviderError('analysis_checkpoint_unavailable')
            extracted = [FindingDraft.model_validate(item) for item in saved['drafts']]
            validate_findings(extracted, chunks[index])
            drafts.extend(extracted)
        for index in range(len(completed), len(chunks)):
            chunk = chunks[index]
            extracted = self.model.extract(run['product'], chunk, recalled)
            validate_findings(extracted, chunk)
            identifier = self.repository.save_extraction(run['_id'], [r['_id'] for r in chunk], extracted)
            completed.append(identifier)
            if not context.checkpoint('analysis', {'chunk_results': completed}):
                raise ProviderError('lease_lost')
            drafts.extend(extracted)
            interval = getattr(self.model, 'extraction_interval_seconds', 0)
            if interval and index + 1 < len(chunks):
                return JobResult(delay_seconds=interval)
        findings = validate_findings(drafts, reviews)
        if len(findings) > self.max_findings:
            if not context.checkpoint('analysis', {'chunk_results': completed, 'error_code': 'analysis_output_limit_exceeded'}):
                raise ProviderError('lease_lost')
            raise ServiceError('analysis_output_limit_exceeded', 422)
        for finding in findings:
            finding['id'] = str(uuid5(NAMESPACE_URL, run['_id'] + ':' + finding['theme']))
        self.repository.stage_findings(run, context.claim.owner_token, findings)
        counts = [{'finding_id': f['id'], 'theme': f['theme'], 'supporting_review_count': f['supporting_review_count']} for f in findings]
        summary = f"{run['product']['title']}: {len(reviews)} scoped reviews; {len(findings)} feedback themes."
        if counts:
            summary += ' Most supported themes: ' + '; '.join(f"{item['theme']} ({item['supporting_review_count']} reviews)" for item in counts[:5]) + '.'
        if guidance:
            summary += ' Recalled PM guidance: ' + ' '.join(d['rationale'] for d in guidance)
        output = {'summary': summary,
            'supporting_review_counts': counts, 'guidance_references': guidance, 'trend': None,
            'limitations': ['Counts describe the selected sample, not product-wide prevalence.',
                            'Quotes are provenance-validated; semantic support remains model interpretation. Up to 20 quotes are shown per theme.',
                            'No comparable time windows were calculated; no trend is claimed.'],
            'investigation_suggestions': [f"Inspect cited reviews for {f['theme']} and validate the reported behavior." for f in findings[:10]]}
        self.repository.stage_output(run['_id'], context.claim.owner_token, output)
        return JobResult(completed=True, updates={'publication_token': context.claim.owner_token})

    def process(self, claim):
        context = JobContext(self.jobs, claim, self.clock, Event(), Event())
        result = self.handle(self.jobs.get(claim), context)
        if result.completed: self.jobs.finish(claim, result.updates, self.clock())
        else: self.jobs.defer(claim, self.clock(), result.delay_seconds)

    def get(self, run_id):
        run = self.repository.get(run_id)
        if run is None: raise ServiceError('run_not_found', 404)
        self.repository.require_readable(run)
        complete = run['status'] == 'completed'
        output = self.repository.output(run) if complete else {}
        current = self.repository.reviews(run['parent_asin'], run['scope'], run['scope'].get('available_through') or self.clock(), self.max_reviews+1)
        stale = [r['_id'] for r in current] != run['review_ids']
        if run['mode'] == 'memory':
            decisions = self.repository.eligible_decisions(run['parent_asin'], run['scope'].get('available_through') or self.clock(), [r['_id'] for r in current])
            stale = stale or [d['_id'] for d in decisions] != run['decision_ids']
        return {'id': run['_id'], 'parent_asin': run['parent_asin'], 'mode': run['mode'], 'scope': run['scope'],
                'status': run['status'], 'created_at': run['created_at'], 'available_through': run['available_through'],
                'snapshot_hash': run['snapshot_hash'], 'denominator': run['denominator'], 'stale': stale,
                'error_code': ('analysis_output_limit_exceeded' if not complete and run.get('processing', {}).get('checkpoints', {}).get('analysis', {}).get('error_code') == 'analysis_output_limit_exceeded' else safe_processing_error(run.get('processing', {}).get('error_code')) if not complete else None),
                **{key: output.get(key) if complete else None for key in ('summary', 'supporting_review_counts', 'guidance_references', 'trend', 'limitations', 'investigation_suggestions')}}

    def findings(self, product_id, run_id):
        run = self.repository.get(run_id)
        if run is None or run['parent_asin'] != product_id: raise ServiceError('run_not_found', 404)
        self.repository.require_readable(run)
        if run['status'] != 'completed': raise ServiceError('run_not_completed', 409)
        fields = ('id', 'issue_type', 'theme', 'description', 'evidence', 'review_ids', 'supporting_review_count', 'provenance_validated', 'semantic_support', 'evidence_sampled')
        return {'run_id': run_id, 'denominator': run['denominator'], 'items': [{key: f[key] for key in fields} for f in self.repository.findings(run)]}
