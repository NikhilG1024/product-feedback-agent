from datetime import datetime, timezone
import json

import httpx
import pytest

from app.domain import MemoryRecord
from app.integrations.hindsight import HindsightMemory
from app.integrations.memory import ProviderError, ReconciliationRequired, RetentionPending


def record():
    return MemoryRecord(id='review-1', content='Synthetic battery lasts two hours.',
                        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc), metadata={'source': 'test'})


def test_pending_retention_returns_only_after_confirmed_completion():
    checkpoints, statuses = [], iter(['pending', 'processing', 'completed'])
    def provider(request):
        if request.method == 'POST':
            payload = json.loads(request.content)
            assert payload['async'] is True
            assert payload['items'][0]['document_id'] == 'review-1'
            assert checkpoints[-1]['status'] == 'submitting'
            return httpx.Response(200, json={'success': True, 'bank_id': 'bank', 'items_count': 1,
                'async': True, 'operation_id': payload['operation_id']})
        return httpx.Response(200, json={'operation_id': request.url.path.rsplit('/', 1)[-1],
                                         'status': next(statuses)})
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider), poll_interval=0)
    assert memory.ensure_retained('bank', record(), checkpoint=checkpoints.append) is None
    assert checkpoints[-1]['status'] == 'completed'



def test_lost_ack_recovers_on_restart_without_duplicate_submission():
    checkpoints, posts = [], []
    def provider(request):
        if request.method == 'POST':
            posts.append(json.loads(request.content))
            raise httpx.ReadTimeout('secret response may have been accepted', request=request)
        return httpx.Response(200, json={'operation_id': posts[0]['operation_id'], 'status': 'completed'})
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider), poll_interval=0)
    with pytest.raises(ReconciliationRequired):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    recovered = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider), poll_interval=0)
    recovered.ensure_retained('bank', record(), state=checkpoints[-1], checkpoint=checkpoints.append)
    assert len(posts) == 1
    assert checkpoints[-1]['status'] == 'completed'


def test_failed_remote_operation_is_terminal_and_sanitized():
    checkpoints = []
    def provider(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'success': True, 'bank_id': 'bank', 'items_count': 1,
                'async': True, 'operation_id': json.loads(request.content)['operation_id']})
        return httpx.Response(200, json={'operation_id': request.url.path.rsplit('/', 1)[-1],
            'status': 'failed', 'error_message': 'secret credential and review content'})
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider), poll_interval=0, timeout=.01)
    with pytest.raises(ProviderError, match='^memory_failed$'):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    assert checkpoints[-1]['status'] == 'failed'


def test_unknown_operation_does_not_resubmit_or_treat_document_as_complete():
    checkpoints, posts = [], []
    def provider(request):
        if request.method == 'POST':
            posts.append(request)
            raise httpx.ReadTimeout('lost acknowledgment', request=request)
        return httpx.Response(404, json={'error': 'not found'})
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider))
    with pytest.raises(ReconciliationRequired):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    for _ in range(2):
        with pytest.raises(ReconciliationRequired):
            memory.ensure_retained('bank', record(), state=checkpoints[-1], checkpoint=checkpoints.append)
    assert len(posts) == 1
    assert checkpoints[-1]['status'] == 'reconciliation_required'


def test_checkpoint_identity_or_content_collision_prevents_remote_call():
    checkpoints = []
    def provider(request):
        if request.method == 'POST':
            raise httpx.ReadTimeout('lost', request=request)
        pytest.fail('Conflicting checkpoint must not reach provider')
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider))
    with pytest.raises(ReconciliationRequired):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    for changed_bank, changed_record in [('other', record()), ('bank', record().model_copy(update={'content': 'changed'}))]:
        with pytest.raises(ProviderError, match='^memory_checkpoint_conflict$'):
            memory.ensure_retained(changed_bank, changed_record, state=checkpoints[-1], checkpoint=checkpoints.append)


def test_oversized_provider_response_is_sanitized_and_reconcilable():
    checkpoints = []
    memory = HindsightMemory('https://memory.test', 'secret', max_response_bytes=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'x' * 101)))
    with pytest.raises(ReconciliationRequired):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    assert checkpoints[-1]['status'] == 'uncertain'


def test_recall_preserves_source_record_ids_and_bounds_context():
    def provider(request):
        assert request.url.path.endswith('/memories/recall')
        assert json.loads(request.content)['types'] == ['world', 'experience']
        return httpx.Response(200, json={'results': [
            {'id': 'fact-a', 'document_id': 'review-1', 'text': 'Battery lasts two hours.'},
            {'id': 'fact-b', 'document_id': 'review-1', 'text': 'Battery is short-lived.'}]})
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider))
    context = memory.recall('bank', 'battery')
    assert context.record_ids == ['review-1']
    assert 'Battery lasts two hours.' in context.text


def test_checkpoint_rejection_prevents_submission():
    memory = HindsightMemory('https://memory.test', 'secret',
        transport=httpx.MockTransport(lambda request: pytest.fail('Lost lease must not write memory')))
    def lost_lease(value):
        raise RuntimeError('lease_lost')
    with pytest.raises(RuntimeError, match='lease_lost'):
        memory.ensure_retained('bank', record(), checkpoint=lost_lease)




def test_pending_operation_timeout_preserves_id_for_resume():
    checkpoints = []
    def provider(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'success': True, 'bank_id': 'bank', 'items_count': 1,
                'async': True, 'operation_id': json.loads(request.content)['operation_id']})
        return httpx.Response(200, json={'operation_id': request.url.path.rsplit('/', 1)[-1], 'status': 'pending'})
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider),
                             timeout=.005, poll_interval=.005)
    with pytest.raises(RetentionPending):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    assert checkpoints[-1]['operation_id'] == checkpoints[0]['operation_id']
    assert checkpoints[-1]['status'] == 'pending'


def test_pending_operation_has_durable_maximum_lifetime(monkeypatch):
    checkpoints = []
    def provider(request):
        if request.method == 'POST':
            raise httpx.ReadTimeout('accepted possibly', request=request)
        pytest.fail('Expired operation requires operator reconciliation')
    memory = HindsightMemory('https://memory.test', 'secret', transport=httpx.MockTransport(provider))
    with pytest.raises(ReconciliationRequired):
        memory.ensure_retained('bank', record(), checkpoint=checkpoints.append)
    monkeypatch.setattr('time.time', lambda: 4000000000)
    with pytest.raises(ReconciliationRequired):
        memory.ensure_retained('bank', record(), state=checkpoints[-1], checkpoint=checkpoints.append)
    assert checkpoints[-1]['status'] == 'reconciliation_required'
