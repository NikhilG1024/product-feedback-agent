from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.domain import AnalysisInput, Scope, DecisionInput
from app.errors import ServiceError
from app.main import create_app
from app.repositories.jobs import JobRepository
from app.services.decisions import DecisionService
from app.services.questions import QuestionService
from app.services.review_processing import ReviewProcessor
from app.worker import Worker
from test_analysis import database, NOW, PM, review, service, decision, Memory, enqueue


def batch(db):
    db.batches.update_one({'_id':'batch-A'}, {'$set':{'end_at':NOW-timedelta(days=1)}})


def test_historical_requires_batch_and_rejects_cutoff_beyond_end(database):
    review(database); batch(database); svc=service(database)
    for scope, code in [(Scope(source='amazon_2023'), 'historical_batch_required'),
                        (Scope(source='amazon_2023',batch_id='batch-A',available_through=NOW), 'cutoff_after_batch_end')]:
        with pytest.raises(ServiceError) as exc: svc.enqueue('P',PM,AnalysisInput(mode='baseline',scope=scope))
        assert exc.value.code==code


def test_historical_defaults_to_batch_end_and_never_uses_future_guidance(database):
    review(database); batch(database)
    decision(database,'old')
    decision(database,'new',decided_at=NOW,created_at=NOW,available_through=NOW)
    svc=service(database,memory=Memory()); run=enqueue(svc,'memory')
    assert run['available_through']==NOW-timedelta(days=1)
    assert run['scope']['available_through']==NOW-timedelta(days=1)
    assert database.analysis_runs.find_one({'_id':run['id']})['decision_ids']==['old']
    decision(database,'later',decided_at=NOW,created_at=NOW,available_through=NOW)
    svc.clock=lambda:NOW+timedelta(days=20)
    assert svc.get(run['id'])['stale'] is False


@pytest.mark.parametrize('status',['pending','completed'])
def test_migrated_legacy_run_all_readers_return_clear_409(database,settings,status):
    from app.migrations.v2 import migrate
    database.analysis_runs.insert_one({'_id':'legacy','parent_asin':'P','batch_id':'batch-A','created_at':NOW,
        'available_through':NOW,'mode':'baseline','status':status})
    migrate(database,False)
    client=TestClient(create_app(settings,SimpleNamespace(analysis=service(database),questions=QuestionService(database,None))))
    headers={'Authorization':'Bearer pm-secret-value'}
    responses=[client.get('/api/v1/analysis-runs/legacy',headers=headers),
        client.get('/api/v1/products/P/findings?run_id=legacy',headers=headers),
        client.post('/api/v1/products/P/questions',headers=headers,json={'run_id':'legacy','question':'Why?'})]
    for response in responses:
        assert response.status_code==409
        assert 'legacy_analysis_unsupported' in response.text
        assert 'new analysis run' in response.text
    assert 'scope' not in database.analysis_runs.find_one({'_id':'legacy'})


def test_analysis_exposes_safe_failure_only(database):
    review(database); svc=service(database); run=enqueue(svc)
    for code, expected in [('provider_failed','provider_failed'),('secret-provider-payload',None)]:
        database.analysis_runs.update_one({'_id':run['id']},{'$set':{'status':'failed','processing.error_code':code}})
        assert svc.get(run['id'])['error_code']==expected


@pytest.mark.parametrize('rationale',['a'*10000,'😀'*10000],ids=['ascii','unicode'])
def test_guidance_combined_size_is_durable_and_retry_freezes_ids(database,rationale):
    from test_review_processing import submit, Memory as ReviewMemory, Model
    for _ in range(7): DecisionService(database).create('P',PM,DecisionInput(kind='preference',rationale=rationale,evidence_ids=[]))
    saved=submit(database)
    memory=ReviewMemory(); processor=ReviewProcessor(database,Model(),memory)
    worker=Worker(JobRepository(database),{'reviews':processor.handle})
    worker.tick()
    frozen=database.reviews.find_one({'_id':saved['id']})['processing']['checkpoints']['classification']
    assert len(frozen['decision_ids'])==7
    DecisionService(database).create('P',PM,DecisionInput(kind='preference',rationale='Too late',evidence_ids=[]))
    memory.pending=False
    worker.clock=lambda:processor.clock()+timedelta(seconds=6)
    worker.tick()
    row=database.reviews.find_one({'_id':saved['id']})
    assert row['processing']['status']=='completed'
    assert set(row['guidance_references'])==set(frozen['decision_ids'])
    assert all(r.content==rationale for key,r in memory.records['review-'+saved['id']].items() if key.startswith('decision:'))


def test_provider_input_budget_is_explicit_without_truncating_guidance(database):
    import httpx
    from app.integrations.llm import DeepSeekModel
    from test_review_processing import submit, Memory as ReviewMemory
    from app.services.reviews import ReviewService
    for _ in range(9): DecisionService(database).create('P',PM,DecisionInput(kind='preference',rationale='😀'*10000,evidence_ids=[]))
    saved=submit(database); memory=ReviewMemory(); memory.pending=False
    def unexpected(request): raise AssertionError('Oversized input must not call provider')
    model=DeepSeekModel('secret',transport=httpx.MockTransport(unexpected))
    Worker(JobRepository(database,max_attempts=1),{'reviews':ReviewProcessor(database,model,memory).handle}).tick()
    assert ReviewService(database).status(saved['id'],PM)['processing']['error_code']=='model_input_too_large'
    assert len(memory.records['review-'+saved['id']])==10
    model.close()
