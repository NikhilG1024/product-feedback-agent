"""Worker orchestration with real persistence and injectable provider handlers."""
from datetime import datetime, timezone

from tests.integration.test_jobs import database, jobs, NOW
from app.worker import Worker, JobResult


def test_worker_only_finishes_explicitly_completed_results(jobs):
    worker = Worker(jobs, {'reviews': lambda job, context: JobResult(completed=True, updates={'classification': 'defect'})}, clock=lambda: NOW)
    assert worker.tick()
    assert not worker.tick()
    assert jobs.claim('reviews', NOW, 180) is None


def test_async_acceptance_stays_pending_and_retains_operation_checkpoint(jobs):
    def handler(job, context):
        assert context.checkpoint('memory', {'operation_id': 'op-1', 'status': 'accepted'})
        return JobResult()
    assert Worker(jobs, {'reviews': handler}, clock=lambda: NOW).tick()
    from datetime import timedelta
    recovered = jobs.claim('reviews', NOW + timedelta(seconds=5), 180)
    assert recovered is not None
    assert jobs.get(recovered)['processing']['attempts'] == 0
    assert jobs.get(recovered)['processing']['checkpoints']['memory']['operation_id'] == 'op-1'


def test_lease_is_renewed_while_handler_is_blocked(jobs):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import sleep
    started, release = Event(), Event()
    def handler(job, context):
        started.set()
        release.wait(5)
        return JobResult(completed=True)
    worker = Worker(jobs, {'reviews': handler}, lease_seconds=1, renewal_interval=0.1)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(worker.tick)
        try:
            assert started.wait(2)
            sleep(1.3)
            assert jobs.claim('reviews', datetime.now(timezone.utc), 1) is None
        finally:
            release.set()
        assert future.result(timeout=3)


def test_shutdown_leaves_uncompleted_work_recoverable(jobs):
    from datetime import timedelta
    def handler(job, context):
        worker.stop()
        return JobResult(completed=True, updates={'classification': 'must-not-publish'})
    worker = Worker(jobs, {'reviews': handler}, clock=lambda: NOW, lease_seconds=10)
    assert worker.tick()
    assert not worker.tick()
    recovered = jobs.claim('reviews', NOW + timedelta(seconds=10), 10)
    assert recovered is not None
    assert 'classification' not in jobs.get(recovered)


def test_bare_provider_operation_id_does_not_mark_success(jobs):
    from datetime import timedelta
    assert Worker(jobs, {'reviews': lambda job, ctx: 'op-1'}, clock=lambda: NOW).tick()
    recovered = jobs.claim('reviews', NOW + timedelta(seconds=2), 180)
    assert recovered is not None
    assert jobs.get(recovered)['processing']['attempts'] == 1


def test_handler_finishing_after_takeover_cannot_publish(jobs):
    from datetime import timedelta
    now = [NOW]
    successor = []
    def handler(job, context):
        now[0] += timedelta(seconds=10)
        successor.append(jobs.claim('reviews', now[0], 10))
        assert not context.checkpoint('memory', {'operation_id': 'stale'})
        return JobResult(completed=True, updates={'classification': 'stale'})
    assert Worker(jobs, {'reviews': handler}, clock=lambda: now[0], lease_seconds=10).tick()
    assert 'classification' not in jobs.get(successor[0])
    assert jobs.finish(successor[0], {'classification': 'correct'}, now[0])


def test_busy_queues_are_served_fairly_without_starvation(database):
    from app.repositories.jobs import JobRepository
    handled=[]
    collections=['analysis_runs','reviews','decisions']
    for collection in collections:
        for index in range(4):
            database[collection].insert_one({'_id':f'{collection}-{index}',
                'processing':{'status':'pending','attempts':0,'next_attempt_at':NOW}})
    worker=Worker(JobRepository(database), {name:(lambda job,ctx,name=name:
        (handled.append(name),JobResult(completed=True))[1]) for name in collections}, clock=lambda:NOW)
    for _ in range(6): assert worker.tick()
    assert handled == collections * 2
