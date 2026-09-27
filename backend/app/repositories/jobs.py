"""Atomic durable queue ownership. All writes are fenced by an unexpired lease."""
from app.repositories.capacity import CapacityGuard
from app.errors import safe_processing_error
from datetime import timedelta
from time import monotonic
from uuid import uuid4

from bson import BSON
from bson.errors import InvalidDocument
from pymongo import ReturnDocument

from app.domain import JobClaim

COLLECTIONS = ('reviews', 'decisions', 'analysis_runs')


class JobRepository:
    def __init__(self, database, max_attempts=5, *, capacity=None):
        if not 1 <= max_attempts <= 100:
            raise ValueError('Invalid maximum attempts')
        self.database = database
        self.capacity = capacity if capacity is not None else CapacityGuard(database)
        self.max_attempts = max_attempts

    def _collection(self, name):
        if name not in COLLECTIONS:
            raise ValueError('Unsupported job collection')
        return self.database[name]

    def claim(self, collection, now, lease_seconds):
        JobClaim.aware_datetime(now)
        if lease_seconds <= 0:
            raise ValueError('Lease must be positive')
        owner = str(uuid4())
        expires = now + timedelta(seconds=lease_seconds)
        values = {'processing.status': 'running', 'processing.owner_token': owner,
                  'processing.lease_expires_at': expires, 'processing.lease_seconds': lease_seconds}
        if collection == 'analysis_runs':
            values['status'] = 'running'
        record = self._collection(collection).find_one_and_update(
            {'$or': [
                {'processing.status': 'pending', 'processing.next_attempt_at': {'$lte': now}},
                {'processing.status': 'running', 'processing.lease_expires_at': {'$lte': now}},
            ]},
            {'$set': values},
            sort=[('processing.next_attempt_at', 1), ('_id', 1)],
            return_document=ReturnDocument.AFTER,
        )
        if record is None:
            return None
        return JobClaim(collection=collection, record_id=record['_id'], owner_token=owner, expires_at=expires)

    def _owned(self, claim, now):
        JobClaim.aware_datetime(now)
        return {'_id': claim.record_id, 'processing.status': 'running',
                'processing.owner_token': claim.owner_token,
                'processing.lease_expires_at': {'$gt': now}}

    def get(self, claim):
        return self._collection(claim.collection).find_one({'_id': claim.record_id})

    def renew(self, claim, now):
        query = self._owned(claim, now)
        record = self._collection(claim.collection).find_one(query)
        if record is None:
            return False
        expires = now + timedelta(seconds=record['processing']['lease_seconds'])
        result = self._collection(claim.collection).update_one(
            query, {'$max': {'processing.lease_expires_at': expires}})
        return result.matched_count == 1

    def finish(self, claim, updates, now):
        started = monotonic()
        self._bounded(updates)
        self.capacity.check_documents([updates])
        values = dict(updates)
        immutable = {'_id', 'processing', 'status', 'parent_asin', 'asin', 'source', 'title', 'text', 'rating',
                     'timestamp', 'timestamp_ms', 'created_at', 'author_id', 'version', 'provenance',
                     'idempotency_key', 'payload_digest', 'batch', 'batch_id', 'held_out', 'dataset_id',
                     'available_through', 'scope', 'mode', 'decided_at', 'kind', 'rationale', 'evidence_ids'}
        if any(key in immutable or '.' in key or key.startswith('$') for key in values):
            raise ValueError('Reserved result field')
        # Preflight may outlast the lease; retain the caller clock's epoch while
        # advancing it by real elapsed time before the atomic ownership check.
        now += timedelta(seconds=monotonic() - started)
        values.update({'processing.status': 'completed', 'processing.completed_at': now})
        if claim.collection == 'analysis_runs':
            values['status'] = 'completed'
        elif claim.collection == 'reviews':
            values['processing.classification_status'] = 'completed'
        result = self._collection(claim.collection).update_one(
            self._owned(claim, now), {'$set': values, '$unset': {
                'processing.owner_token': '', 'processing.lease_expires_at': '', 'processing.error_code': ''}})
        return result.matched_count == 1

    def retry(self, claim, error_code, now):
        query = self._owned(claim, now)
        record = self._collection(claim.collection).find_one(query)
        if record is None:
            return False
        attempts = record['processing'].get('attempts', 0) + 1
        status = 'failed' if attempts >= self.max_attempts else 'pending'
        values = {
            'processing.status': status,
            'processing.attempts': attempts,
            'processing.next_attempt_at': now + timedelta(seconds=max(60 if error_code == 'model_rate_limited' else 0, min(300, 2 ** attempts))),
            'processing.error_code': safe_processing_error(error_code) or 'provider_failed',
        }
        if status == 'failed' and claim.collection in {'reviews', 'decisions'}:
            if record['processing'].get('memory_status') != 'synced':
                values['processing.memory_status'] = 'failed'
            if claim.collection == 'reviews':
                values['processing.classification_status'] = 'failed'
        if claim.collection == 'analysis_runs':
            values['status'] = status
        result = self._collection(claim.collection).update_one(query, {'$set': values,
            '$unset': {'processing.owner_token': '', 'processing.lease_expires_at': ''}})
        return result.matched_count == 1

    def checkpoint(self, claim, stage, value, now):
        """Persist provider progress before returning; survives retry and lease recovery."""
        if stage not in ('memory', 'classification', 'analysis'):
            raise ValueError('Unsupported checkpoint stage')
        started = monotonic()
        self._bounded(value)
        self.capacity.check_documents([value])
        now += timedelta(seconds=monotonic() - started)
        result = self._collection(claim.collection).update_one(
            self._owned(claim, now), {'$set': {f'processing.checkpoints.{stage}': value}})
        return result.matched_count == 1

    def stage_status(self, claim, stage, status, now):
        if (stage, status) not in {('memory', 'synced')}:
            raise ValueError('Unsupported stage status')
        return self._collection(claim.collection).update_one(self._owned(claim, now),
            {'$set': {f'processing.{stage}_status': status}}).matched_count == 1

    def defer(self, claim, now, delay_seconds=5):
        """Await an accepted asynchronous operation without counting a failure."""
        if not 1 <= delay_seconds <= 300:
            raise ValueError('Invalid defer interval')
        values = {'processing.status': 'pending',
                  'processing.next_attempt_at': now + timedelta(seconds=delay_seconds)}
        if claim.collection == 'analysis_runs':
            values['status'] = 'pending'
        result = self._collection(claim.collection).update_one(self._owned(claim, now), {
            '$set': values,
            '$unset': {'processing.owner_token': '', 'processing.lease_expires_at': ''},
        })
        return result.matched_count == 1

    @staticmethod
    def _bounded(value):
        if not isinstance(value, dict):
            raise ValueError('Result or checkpoint must be an object')
        try:
            encoded = BSON.encode(value, check_keys=True)
        except (InvalidDocument, TypeError, ValueError):
            raise ValueError('Invalid result or checkpoint') from None
        if len(encoded) > 65536:
            raise ValueError('Result or checkpoint exceeds limit')
