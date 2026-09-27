import asyncio
from dataclasses import replace, fields
from types import SimpleNamespace
import pytest
from app.errors import ServiceError
from tests.test_analysis import database, review, service, enqueue, NOW, PM

class StatsDB:
    def __init__(self, stats): self.stats=stats
    def command(self,*args,**kwargs):
        if isinstance(self.stats,Exception): raise self.stats
        return self.stats
class StatsClient:
    def __init__(self, stats):
        self.admin=StatsDB({'databases':[{'name':n} for n in stats]}); self.stats=stats
    def __getitem__(self,name): return StatsDB(self.stats[name])

def guard(stats,**kwargs):
    from app.repositories.capacity import CapacityGuard
    return CapacityGuard(SimpleNamespace(client=StatsClient(stats)),**kwargs)

def test_capacity_sums_data_and_indexes_across_all_user_databases():
    guard({'app':{'dataSize':200_000_000,'indexSize':1,'storageSize':999_999_999},
           'other':{'dataSize':199_000_000,'indexSize':1},'admin':{}},reserve_bytes=1).check_write()
    with pytest.raises(ServiceError) as exc:
        guard({'app':{'dataSize':200_000_000,'indexSize':1},'other':{'dataSize':199_999_998,'indexSize':1}},reserve_bytes=1).check_write()
    assert exc.value.status_code==503 and exc.value.code=='capacity_exceeded'

@pytest.mark.parametrize('stats',[{}, {'dataSize':0}, {'dataSize':float('nan'),'indexSize':0}, RuntimeError('secret-password')])
def test_missing_or_invalid_required_telemetry_fails_closed(stats):
    with pytest.raises(ServiceError) as exc: guard({'app':stats}).check_write()
    assert exc.value.code=='capacity_unavailable' and 'secret-password' not in str(exc.value)

def test_numeric_settings_reject_nonpositive_values(settings):
    for field in fields(settings):
        if type(getattr(settings,field.name)) is int:
            with pytest.raises(ValueError): replace(settings,**{field.name:0})
            with pytest.raises(ValueError): replace(settings,**{field.name:-1})

@pytest.mark.parametrize('origin',['https://u:p@example.com','https://example.com?x=1','https://example.com#x'])
def test_cors_rejects_non_origin_components(settings,origin):
    with pytest.raises(ValueError): replace(settings,cors_origins=(origin,))

def test_streamed_overflow_is_rejected_before_route():
    from app.main import BodyLimitMiddleware
    called=[]; sent=[]
    async def app(*args): called.append(True)
    messages=iter([{'type':'http.request','body':b'abc','more_body':True},{'type':'http.request','body':b'def','more_body':False}])
    async def receive(): return next(messages)
    async def send(message): sent.append(message)
    asyncio.run(BodyLimitMiddleware(app,5)({'type':'http'},receive,send))
    assert not called and sent[0]['status']==413

def test_throttle_is_shared_across_service_instances(database):
    from app.services.reviews import ReviewService
    from app.domain import Principal, ReviewInput
    payload=ReviewInput(title='Review',text='Useful chair.',rating=4)
    principal=Principal(user_id='reviewer',role='reviewer')
    ReviewService(database,submission_limit=1).submit('P',principal,'one',payload)
    with pytest.raises(ServiceError) as exc: ReviewService(database,submission_limit=1).submit('P',principal,'two',payload)
    assert exc.value.status_code==429
    assert database.reviews.count_documents({'source':'user_submission'})==1

def test_guards_block_new_writes_and_worker_growth(database):
    from app.repositories.capacity import CapacityGuard
    from app.repositories.reviews import ReviewRepository
    from app.repositories.analysis import AnalysisRepository
    from app.repositories.decisions import DecisionRepository
    from app.repositories.jobs import JobRepository
    from app.domain import ReviewInput, DecisionInput
    capacity=CapacityGuard(database,capacity_bytes=1,reserve_bytes=1)
    with pytest.raises(ServiceError): ReviewRepository(database,capacity=capacity).consume('u',NOW,10)
    with pytest.raises(ServiceError): ReviewRepository(database,capacity=capacity).create_submission('P','u','k',ReviewInput(title='T',text='X',rating=1),'d',NOW)
    with pytest.raises(ServiceError): DecisionRepository(database,capacity=capacity).create('P',PM,DecisionInput(kind='decision',rationale='Check',evidence_ids=[]),NOW)
    review(database); run=enqueue(service(database))
    repo=AnalysisRepository(database,capacity=capacity)
    with pytest.raises(ServiceError): repo.create({'_id':'P'}, {}, 'baseline', [], [], NOW,NOW)
    with pytest.raises(ServiceError): repo.stage_output(run['id'],'token',{'summary':'text'})
    with pytest.raises(ServiceError): repo.stage_findings({'_id':run['id'],'parent_asin':'P'},'token',[{'id':'f'}])
    jobs=JobRepository(database,capacity=capacity)
    claim=jobs.claim('analysis_runs',NOW,180)
    with pytest.raises(ServiceError): jobs.checkpoint(claim,'analysis',{'x':'y'},NOW)
    with pytest.raises(ServiceError): jobs.finish(claim,{'summary':'text'},NOW)
    assert database.decisions.count_documents({})==0
    assert database.analysis_outputs.count_documents({})==0
    assert database.findings.count_documents({})==0

def test_capacity_failure_health_and_errors_never_expose_secrets(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    settings=replace(settings,llm_api_key='llm_secret')
    def ready(): raise RuntimeError('mongodb://secret-password@host')
    app=create_app(settings,SimpleNamespace(check_ready=ready))
    @app.get('/failure')
    def failure(): raise ServiceError('llm_secret',503)
    client=TestClient(app)
    assert client.get('/health/ready').json()=={'status':'unavailable'}
    response=client.get('/failure')
    assert response.status_code==503 and 'llm_secret' not in response.text

def test_capacity_bypass_only_explicit_local_test_settings(settings):
    with pytest.raises(ValueError): replace(settings,capacity_checks_enabled=False)
    with pytest.raises(ValueError): replace(settings,mongo_uri='mongodb://remote.example',mongo_database='test_safe',capacity_checks_enabled=False)
    assert replace(settings,mongo_database='test_safe',capacity_checks_enabled=False).capacity_checks_enabled is False

def test_capacity_reserves_proposed_growth_and_retry_remains_operational(database):
    from app.repositories.jobs import JobRepository
    from app.repositories.capacity import CapacityGuard
    from app.worker import Worker, JobResult
    review(database); run=enqueue(service(database))
    blocked=CapacityGuard(database,capacity_bytes=1,reserve_bytes=1)
    jobs=JobRepository(database,capacity=blocked)
    assert Worker(jobs,{'analysis_runs':lambda *_: JobResult(completed=True,updates={'large':'payload'})},clock=lambda:NOW).tick()
    doc=database.analysis_runs.find_one({'_id':run['id']})
    assert doc['status']=='pending' and doc['processing']['attempts']==1
    assert 'large' not in doc
    with pytest.raises(ServiceError): guard({'app':{'dataSize':399_000_000,'indexSize':0}},reserve_bytes=1).check_write(estimated_bytes=1_000_000)

def test_configured_readiness_requires_global_capacity_telemetry(settings,monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import configured_app
    import app.repositories.mongo
    db=SimpleNamespace(client=StatsClient({'app':{'dataSize':400_000_000,'indexSize':0}}),command=lambda *_:{'ok':1})
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls: settings))
    monkeypatch.setattr(app.repositories.mongo,'connect',lambda settings:(SimpleNamespace(close=lambda:None),db))
    response=TestClient(configured_app()).get('/health/ready')
    assert response.status_code==503 and response.json()=={'status':'unavailable'}


def test_capacity_accepts_atlas_bson_int64_and_enforces_limit():
    from bson.int64 import Int64
    guard({'app': {'dataSize': Int64(211419886), 'indexSize': Int64(80318464)}}).check_write()
    with pytest.raises(ServiceError) as exc:
        guard({'app': {'dataSize': Int64(399000000), 'indexSize': Int64(1)}}).check_write()
    assert exc.value.code == 'capacity_exceeded'


@pytest.mark.parametrize('value', [True, False, '123', -1, float('inf')])
def test_capacity_rejects_invalid_numeric_types_and_values(value):
    with pytest.raises(ServiceError) as exc:
        guard({'app': {'dataSize': value, 'indexSize': 0}}).check_write()
    assert exc.value.code == 'capacity_unavailable'
