"""Real local Mongo tests of the durable job ownership boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_reviews import database
from app.domain import ReviewInput
from app.repositories.reviews import ReviewRepository
from app.repositories.jobs import JobRepository

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)

@pytest.fixture
def jobs(database):
    from app.migrations.v2 import migrate
    migrate(database, False)
    ReviewRepository(database).create_submission('P', 'U', 'K', ReviewInput(title='Broken', text='Hinge failed', rating=2), 'digest', NOW)
    return JobRepository(database)


def test_concurrent_claimers_have_exactly_one_owner(jobs):
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: jobs.claim('reviews', NOW, 180), range(8)))
    assert len([claim for claim in claims if claim is not None]) == 1


def test_expired_owner_cannot_write_and_successor_recovers(jobs):
    old = jobs.claim('reviews', NOW, 10)
    expiry = NOW + timedelta(seconds=10)
    assert jobs.renew(old, expiry) is False
    assert jobs.finish(old, {'classification': 'wrong'}, expiry) is False
    assert jobs.retry(old, 'provider_failed', expiry) is False
    successor = jobs.claim('reviews', expiry, 10)
    assert successor and successor.owner_token != old.owner_token
    assert jobs.finish(old, {}, expiry) is False
    assert jobs.finish(successor, {'classification': 'correct'}, expiry) is True
    record = jobs.get(successor)
    assert record['processing']['status'] == 'completed'
    assert record['classification'] == 'correct'


def test_renewal_extends_live_ownership(jobs):
    claim = jobs.claim('reviews', NOW, 10)
    assert jobs.renew(claim, NOW + timedelta(seconds=5)) is True
    assert jobs.claim('reviews', NOW + timedelta(seconds=11), 10) is None
    assert jobs.finish(claim, {}, NOW + timedelta(seconds=11)) is True


def test_five_failures_are_terminal_and_errors_are_sanitized(jobs):
    now = NOW
    for delay in (2, 4, 8, 16, 32):
        claim = jobs.claim('reviews', now, 180)
        assert claim is not None
        assert jobs.retry(claim, 'token=secret https://private.example', now)
        assert jobs.claim('reviews', now, 180) is None
        now += timedelta(seconds=delay)
    assert jobs.get(claim)['processing']['status'] == 'failed'
    assert jobs.get(claim)['processing']['attempts'] == 5
    assert 'secret' not in str(jobs.get(claim))
    assert jobs.claim('reviews', now, 180) is None


def test_provider_checkpoint_survives_recovery_and_is_fenced(jobs):
    claim = jobs.claim('reviews', NOW, 10)
    assert jobs.checkpoint(claim, 'memory', {'operation_id': 'op-1', 'status': 'accepted'}, NOW)
    successor = jobs.claim('reviews', NOW + timedelta(seconds=10), 10)
    assert not jobs.checkpoint(claim, 'memory', {'status': 'completed'}, NOW + timedelta(seconds=10))
    assert jobs.get(successor)['processing']['checkpoints']['memory']['operation_id'] == 'op-1'


@pytest.mark.parametrize('collection', ['decisions', 'analysis_runs'])
def test_all_record_types_have_durable_queue_transitions(database, collection):
    from app.migrations.v2 import migrate
    migrate(database, False)
    record = {'_id': 'job', 'parent_asin': 'P', 'available_through': NOW,
              'processing': {'status': 'pending', 'attempts': 0, 'next_attempt_at': NOW}}
    if collection == 'decisions':
        record.update(decided_at=NOW, kind='prioritize', rationale='Frequent failure')
    else:
        record.update(created_at=NOW, batch_id='batch-a', mode='memory', status='pending')
    database[collection].insert_one(record)
    repository = JobRepository(database)
    claim = repository.claim(collection, NOW, 10)
    assert claim
    if collection == 'analysis_runs':
        assert repository.get(claim)['status'] == 'running'
    assert repository.finish(claim, {}, NOW)
    assert repository.get(claim)['processing']['status'] == 'completed'
    if collection == 'analysis_runs':
        assert repository.get(claim)['status'] == 'completed'


def test_expired_running_claim_is_atomic(jobs):
    jobs.claim('reviews', NOW, 1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: jobs.claim('reviews', NOW + timedelta(seconds=1), 10), range(8)))
    assert len([claim for claim in claims if claim is not None]) == 1


def test_results_cannot_overwrite_review_input_or_exceed_document_budget(jobs):
    claim = jobs.claim('reviews', NOW, 180)
    for updates in ({'text': 'rewritten'}, {'_id': 'replacement'}, {'processing.status': 'completed'}, {'output': 'x' * 65536}):
        with pytest.raises(ValueError):
            jobs.finish(claim, updates, NOW)
    assert jobs.get(claim)['text'] == 'Hinge failed'

@pytest.mark.parametrize('operation', ['finish', 'checkpoint'])
def test_capacity_preflight_crossing_expiry_cannot_publish_without_successor(jobs, monkeypatch, operation):
    import app.repositories.jobs as module
    elapsed = [0.0]
    monkeypatch.setattr(module, 'monotonic', lambda: elapsed[0], raising=False)
    class SlowCapacity:
        def check_documents(self, documents): elapsed[0] = 11.0
    jobs.capacity = SlowCapacity()
    claim = jobs.claim('reviews', NOW, 10)
    if operation == 'finish':
        result = jobs.finish(claim, {'classification': 'late'}, NOW)
    else:
        result = jobs.checkpoint(claim, 'memory', {'operation_id': 'late'}, NOW)
    assert result is False
    record = jobs.get(claim)
    assert record['processing']['status'] == 'running'
    assert record['processing']['owner_token'] == claim.owner_token
    assert 'classification' not in record
    assert 'checkpoints' not in record['processing']


def test_model_rate_limit_is_visible_and_retries_wait_one_minute(jobs):
    now = NOW
    for attempt in range(5):
        claim = jobs.claim('reviews', now, 180)
        assert claim is not None
        assert jobs.retry(claim, 'model_rate_limited', now)
        row = jobs.get(claim)
        assert row['processing']['error_code'] == 'model_rate_limited'
        assert jobs.claim('reviews', now + timedelta(seconds=59), 180) is None
        now += timedelta(seconds=60)
    assert row['processing']['status'] == 'failed'
    assert jobs.claim('reviews', now, 180) is None
