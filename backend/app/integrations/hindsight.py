"""Synchronous Hindsight adapter; accepted writes are polled, never blindly retried."""
import hashlib
import json
import math
import time
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from hindsight_client_api.models import OperationStatusResponse, RetainResponse
from pydantic import ValidationError

from app.domain import MemoryRecord
from app.integrations.http import JSONTransport, ProviderHTTPError
from app.integrations.memory import Checkpoint, ProviderError, RetentionPending, ReconciliationRequired


class HindsightMemory:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60,
                 poll_interval: float = 1, max_response_bytes: int = 262144,
                 max_operation_age_seconds: float = 900, transport=None):
        self.http = JSONTransport(base_url, api_key, timeout=timeout,
                                  max_response_bytes=max_response_bytes, transport=transport)
        if (not math.isfinite(poll_interval) or poll_interval < 0
                or not math.isfinite(max_operation_age_seconds) or max_operation_age_seconds <= 0):
            raise ValueError('Invalid polling interval')
        self.timeout, self.poll_interval = timeout, poll_interval
        self.max_operation_age_seconds = max_operation_age_seconds

    def close(self):
        self.http.close()

    def ensure_retained(self, bank_id: str, record: MemoryRecord, *,
                        state: dict | None = None, checkpoint: Checkpoint | None = None) -> None:
        if checkpoint is None:
            raise ProviderError('memory_checkpoint_required')
        fingerprint = hashlib.sha256(json.dumps(record.model_dump(mode='json'), sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        resuming = state is not None
        state = dict(state) if state is not None else {
            'bank_id': bank_id, 'record_id': record.id, 'fingerprint': fingerprint,
            'operation_id': str(uuid5(NAMESPACE_URL, json.dumps([bank_id, record.id, fingerprint]))),
            'status': 'submitting', 'started_at': time.time()}
        if (state.get('bank_id') != bank_id or state.get('record_id') != record.id
                or state.get('fingerprint') != fingerprint or not isinstance(state.get('operation_id'), str)
                or not state['operation_id']):
            raise ProviderError('memory_checkpoint_conflict')
        if state.get('status') == 'completed':
            return
        if state.get('status') in {'failed', 'cancelled'}:
            raise ProviderError('memory_failed')
        started = state.get('started_at')
        if not isinstance(started, (float, int)) or not math.isfinite(started):
            raise ProviderError('memory_checkpoint_conflict')
        remaining_lifetime = self.max_operation_age_seconds - (time.time() - started)
        if remaining_lifetime <= 0:
            state['status'] = 'reconciliation_required'
            checkpoint(dict(state))
            raise ReconciliationRequired()
        deadline = time.monotonic() + min(self.timeout, remaining_lifetime)
        path = f'/v1/default/banks/{quote(bank_id, safe="")}'
        if not resuming:
            # A lease-fenced failure here MUST prevent sending the request.
            checkpoint(dict(state))
            try:
                response = self.http.request('POST', path + '/memories', deadline=deadline, payload={
                    'async': True, 'operation_id': state['operation_id'],
                    'items': [{'content': record.content, 'document_id': record.id,
                               'timestamp': record.occurred_at.isoformat(),
                               'metadata': {k: v if isinstance(v, str) else json.dumps(v) for k, v in record.metadata.items()}}]})
                retained = RetainResponse.model_validate(response)
                if (not retained.success or not retained.var_async or retained.bank_id != bank_id
                        or retained.items_count != 1 or not retained.operation_id):
                    raise ProviderError('memory_invalid_response')
            except (ProviderError, ValidationError):
                state['status'] = 'uncertain'
                checkpoint(dict(state))
                raise ReconciliationRequired() from None
            # Older servers may assign their own operation ID; preserve the actual acknowledgement.
            state.update(operation_id=retained.operation_id, status='pending')
            checkpoint(dict(state))
        while time.monotonic() < deadline:
            try:
                response = self.http.request('GET', path + '/operations/' + quote(state['operation_id'], safe=''), deadline=deadline)
                result = OperationStatusResponse.model_validate(response)
                if result.operation_id != state['operation_id']:
                    raise ProviderError('memory_invalid_response')
            except ProviderHTTPError as error:
                if error.status == 404:
                    state['status'] = 'reconciliation_required'
                    checkpoint(dict(state))
                    raise ReconciliationRequired() from None
                raise ProviderError('memory_poll_failed') from None
            except (ProviderError, ValidationError):
                raise ProviderError('memory_poll_failed') from None
            if result.status == 'completed':
                state['status'] = 'completed'
                checkpoint(dict(state))
                return
            if result.status in {'failed', 'cancelled'}:
                state['status'] = result.status
                checkpoint(dict(state))
                raise ProviderError('memory_failed')
            if result.status not in {'pending', 'processing'}:
                state['status'] = 'reconciliation_required'
                checkpoint(dict(state))
                raise ReconciliationRequired()
            time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        raise RetentionPending()

    def recall(self, bank_id: str, query: str):
        from hindsight_client_api.models import RecallResponse
        from app.domain import MemoryContext
        if not query.strip() or len(query) > 2000:
            raise ProviderError('memory_query_invalid')
        try:
            response = self.http.request('POST', f'/v1/default/banks/{quote(bank_id, safe="")}/memories/recall',
                payload={'query': query, 'types': ['world', 'experience'], 'budget': 'low', 'max_tokens': 4096})
            recalled = RecallResponse.model_validate(response)
            if len(recalled.results) > 100 or any(not item.document_id for item in recalled.results):
                raise ProviderError('memory_invalid_response')
            context = MemoryContext(text='\n'.join(f'[{item.document_id}] {item.text}' for item in recalled.results),
                                    record_ids=list(dict.fromkeys(item.document_id for item in recalled.results)))
            if len(context.text) > 20000:
                raise ProviderError('memory_output_too_large')
            return context
        except ValidationError:
            raise ProviderError('memory_invalid_response') from None
