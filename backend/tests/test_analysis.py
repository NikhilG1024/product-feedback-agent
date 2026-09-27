from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import os
import pytest
from pymongo import MongoClient
from app.domain import AnalysisInput, Scope, Principal, FindingDraft, Evidence, MemoryContext
from app.errors import ServiceError
from app.worker import Worker
from app.repositories.jobs import JobRepository

NOW=datetime(2025,1,10,tzinfo=timezone.utc)
PM=Principal(user_id='pm', role='pm')

@pytest.fixture
def database():
    client=MongoClient(os.getenv('TEST_MONGODB_URI', 'mongodb://127.0.0.1:27029'),tz_aware=True,serverSelectionTimeoutMS=2000)
    name='test_task5_'+uuid4().hex
    db=client[name]
    from app.migrations.v2 import migrate
    migrate(db,False)
    db.products.insert_one({'_id':'P','title':'Folding chair','provenance':{}})
    db.batches.insert_one({'_id':'batch-A','dataset_id':'dataset','label':'A','held_out':False,'start_at':NOW-timedelta(days=10),'end_at':NOW,'review_count':0,'product_counts':{}})
    yield db
    client.drop_database(name)
    client.close()


def review(db, key='r1', **changes):
    doc={'_id':key,'parent_asin':'P','asin':'V','title':'Review','text':'The hinge broke.', 'rating':2,
        'timestamp':NOW-timedelta(days=2),'timestamp_ms':1,'batch':'A','batch_id':'batch-A','held_out':False,'dataset_id':'dataset','provenance':{}}
    doc.update(changes)
    db.reviews.insert_one(doc)


class Model:
    def __init__(self): self.calls=[]
    def extract(self, product, reviews, context):
        self.calls.append((product,reviews,context))
        return [FindingDraft(issue_type='reported_defect',theme=' HINGE ' if len(self.calls)%2 else 'hinge',description='Hinge reports',
            evidence=[Evidence(review_id=r['_id'],quote='The hinge broke.') for r in reviews])]


class NoMemory:
    def __getattr__(self,name): raise AssertionError('Baseline used memory')


def service(db, model=None, memory=None):
    from app.services.analysis import AnalysisService
    return AnalysisService(db, model or Model(), memory or NoMemory(), clock=lambda:NOW)


def enqueue(svc, mode='baseline', **scope):
    return svc.enqueue('P',PM,AnalysisInput(mode=mode,scope=Scope(source='amazon_2023',**{'batch_id':'batch-A',**scope})))


def work(svc,db):
    worker=Worker(JobRepository(db),{'analysis_runs':svc.handle},clock=lambda:NOW)
    assert worker.tick()


def test_snapshot_excludes_late_review_and_incomplete_results(database):
    review(database)
    model=Model(); svc=service(database,model)
    run=enqueue(svc)
    assert svc.get(run['id'])['summary'] is None
    with pytest.raises(ServiceError): svc.findings('P',run['id'])
    review(database,'r2')
    work(svc,database)
    result=svc.get(run['id'])
    assert result['status']=='completed'
    assert result['denominator']==1 and result['stale'] is True
    assert result['trend'] is None
    assert 'Folding chair' in result['summary']
    assert result['guidance_references']==[]
    assert svc.findings('P',run['id'])['items'][0]['supporting_review_count']==1
    assert [r['_id'] for _,rs,_ in model.calls for r in rs]==['r1']

class Memory:
    def __init__(self): self.records={}; self.context_ids=None
    def ensure_retained(self, bank_id, record, *, state, checkpoint):
        checkpoint({'record_id':record.id,'status':'completed'})
        self.records.setdefault(bank_id,{})[record.id]=record
    def recall(self, bank_id, query):
        records=self.records[bank_id]
        ids=self.context_ids if self.context_ids is not None else list(records)
        return MemoryContext(text='Remember PM guidance',record_ids=ids)


def decision(db,key,**changes):
    doc={'_id':key,'parent_asin':'P','decided_at':NOW-timedelta(days=4),'available_through':NOW-timedelta(days=3),
         'kind':'investigate','rationale':'Check hinge batches before prioritizing.','evidence_ids':[]}
    doc.update(changes); db.decisions.insert_one(doc)


def test_memory_uses_only_snapshot_eligible_guidance_and_equal_denominators(database):
    review(database)
    decision(database,'eligible')
    decision(database,'future',decided_at=NOW+timedelta(days=1))
    decision(database,'unavailable',available_through=NOW+timedelta(days=1))
    decision(database,'foreign',parent_asin='other')
    decision(database,'outside',evidence_ids=['held-out'])
    mem=Memory(); model=Model(); svc=service(database,model,mem)
    baseline=enqueue(svc)
    run=enqueue(svc,'memory')
    decision(database,'late-arrival')
    work(svc,database); work(svc,database)
    result=svc.get(run['id'])
    assert result['denominator']==svc.get(baseline['id'])['denominator']==1
    assert result['guidance_references']==[{'decision_id':'eligible','kind':'investigate','rationale':'Check hinge batches before prioritizing.'}]
    assert 'Check hinge batches' in result['summary']
    assert len(mem.records)==1
    assert set(next(iter(mem.records.values())))=={'review:r1','decision:eligible'}
    assert any(call[2].record_ids==['review:r1','decision:eligible'] for call in model.calls)


def test_cutoff_source_product_and_heldout_restrictions(database):
    review(database,'eligible')
    review(database,'future-review',timestamp=NOW+timedelta(days=1))
    review(database,'late-created',created_at=NOW+timedelta(days=1))
    review(database,'other-product',parent_asin='OTHER')
    review(database,'held-out',batch='C',batch_id='batch-C',held_out=True)
    database.batches.insert_one({'_id':'batch-C','dataset_id':'dataset','label':'C','held_out':True,'start_at':NOW,'end_at':NOW,'review_count':1,'product_counts':{}})
    svc=service(database)
    result=enqueue(svc,available_through=NOW)
    work(svc,database)
    assert svc.findings('P',result['id'])['items'][0]['review_ids']==['eligible']
    with pytest.raises(ServiceError) as error: enqueue(svc,batch_id='batch-C')
    assert error.value.code=='evaluation_scope_required'
    assert enqueue(svc,batch_id='batch-C',evaluation=True)['denominator']==1
    with pytest.raises(ServiceError): svc.enqueue('P',PM,AnalysisInput(mode='baseline',scope=Scope(source='user_submission')))


def test_chunk_boundaries_and_theme_merge(database):
    for i in range(43): review(database,f'r{i:02}')
    model=Model(); svc=service(database,model)
    result=enqueue(svc); work(svc,database)
    assert [len(call[1]) for call in model.calls]==[5,5,5,5,5,5,5,5,3]
    assert [r['_id'] for call in model.calls for r in call[1]] == [f'r{i:02}' for i in range(43)]
    assert len({r['_id'] for _,rs,_ in model.calls for r in rs})==43
    findings=svc.findings('P',result['id'])['items']
    assert len(findings)==1 and findings[0]['supporting_review_count']==43
    assert svc.get(result['id'])['supporting_review_counts'][0]['supporting_review_count']==43


def test_scope_limit_rejects_before_processing(database):
    for i in range(1501): review(database,str(i))
    with pytest.raises(ServiceError) as error: enqueue(service(database))
    assert error.value.code=='narrower_scope_required'


def test_character_budget_and_oversized_record(database):
    for i in range(5): review(database,str(i),text='The hinge broke.'+'a'*734)
    model=Model(); svc=service(database,model)
    result=enqueue(svc); work(svc,database)
    assert [len(call[1]) for call in model.calls]==[4,1]
    review(database,'oversize',text='x'*3001)
    with pytest.raises(ServiceError) as error: enqueue(svc)
    assert error.value.code=='review_exceeds_model_budget'


def test_expired_worker_output_cannot_publish_or_override_successor(database):
    from app.worker import JobContext
    from threading import Event
    review(database); svc=service(database); result=enqueue(svc)
    jobs=JobRepository(database)
    old=jobs.claim('analysis_runs',NOW,1)
    old_record=jobs.get(old)
    new=jobs.claim('analysis_runs',NOW+timedelta(seconds=2),180)
    winner=svc.handle(jobs.get(new),JobContext(jobs,new,lambda:NOW+timedelta(seconds=2),Event(),Event()))
    assert jobs.finish(new,winner.updates,NOW+timedelta(seconds=2))
    original=svc.findings('P',result['id'])
    from app.integrations.memory import ProviderError
    with pytest.raises(ProviderError, match='lease_lost'):
        svc.handle(old_record,JobContext(jobs,old,lambda:NOW+timedelta(seconds=3),Event(),Event()))
    assert not jobs.finish(old,{},NOW+timedelta(seconds=3))
    assert svc.findings('P',result['id'])==original


def test_memory_progress_is_bounded_and_resumes(database):
    for i in range(25): review(database,str(i))
    mem=Memory(); svc=service(database,memory=mem)
    run=enqueue(svc,'memory'); work(svc,database)
    assert svc.get(run['id'])['status']=='pending'
    assert len(next(iter(mem.records.values())))==20
    svc.clock=lambda:NOW+timedelta(seconds=6)
    Worker(JobRepository(database),{'analysis_runs':svc.handle},clock=svc.clock).tick()
    assert svc.get(run['id'])['status']=='completed'
    assert len(next(iter(mem.records.values())))==25


def test_run_routes_require_pm_and_publish_explicit_shapes(database,settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    review(database); svc=service(database)
    client=TestClient(create_app(settings,SimpleNamespace(analysis=svc)))
    headers={'Authorization':'Bearer pm-secret-value'}
    route='/api/v1/products/P/analysis-runs'
    response=client.post(route,headers=headers,json={'mode':'baseline','scope':{'source':'amazon_2023','batch_id':'batch-A'}})
    assert response.status_code==202
    run_id=response.json()['id']
    assert client.get('/api/v1/analysis-runs/'+run_id,headers=headers).json()['summary'] is None
    assert client.get('/api/v1/products/P/findings',params={'run_id':run_id},headers=headers).status_code==409
    work(svc,database)
    assert client.get('/api/v1/products/P/findings',params={'run_id':run_id},headers=headers).json()['items'][0]['supporting_review_count']==1
    assert client.get('/api/v1/analysis-runs/'+run_id,headers={'Authorization':'Bearer reviewer-secret-value'}).status_code==403
    assert 'publication_token' not in client.get('/api/v1/analysis-runs/'+run_id,headers=headers).text


def test_uncertain_retention_restores_exact_checkpoint_on_restart(database):
    from app.integrations.memory import RetentionPending
    class PendingMemory(Memory):
        def __init__(self): super().__init__(); self.states=[]
        def ensure_retained(self, bank_id, record, *, state, checkpoint):
            self.states.append(state)
            if len(self.states)==1:
                checkpoint({'operation_id':'accepted-operation','record_id':record.id,'status':'pending'})
                raise RetentionPending()
            assert state=={'operation_id':'accepted-operation','record_id':'review:r1','status':'pending'}
            super().ensure_retained(bank_id,record,state=state,checkpoint=checkpoint)
    review(database); memory=PendingMemory(); svc=service(database,memory=memory)
    run=enqueue(svc,'memory'); work(svc,database)
    assert svc.get(run['id'])['summary'] is None
    restarted=service(database,memory=memory); restarted.clock=lambda:NOW+timedelta(seconds=6)
    Worker(JobRepository(database),{'analysis_runs':restarted.handle},clock=restarted.clock).tick()
    assert restarted.get(run['id'])['status']=='completed'
    assert len(memory.states)==2


def test_lost_lease_blocks_retention_before_external_write(database):
    from app.worker import JobContext
    from app.integrations.memory import ProviderError
    from threading import Event
    review(database); memory=Memory(); svc=service(database,memory=memory)
    enqueue(svc,'memory'); jobs=JobRepository(database)
    claim=jobs.claim('analysis_runs',NOW,1)
    with pytest.raises(ProviderError):
        svc.handle(jobs.get(claim),JobContext(jobs,claim,lambda:NOW+timedelta(seconds=2),Event(),Event()))
    assert memory.records=={}


def test_foreign_recalled_provenance_never_publishes(database):
    review(database); memory=Memory(); memory.context_ids=['review:foreign']
    svc=service(database,memory=memory); run=enqueue(svc,'memory'); work(svc,database)
    assert svc.get(run['id'])['status']=='pending'
    assert svc.get(run['id'])['summary'] is None


def test_new_guidance_marks_live_memory_run_stale(database):
    review(database); svc=service(database,memory=Memory())
    run=enqueue(svc,'memory'); work(svc,database)
    assert svc.get(run['id'])['stale'] is False
    decision(database,'new-guidance')
    assert svc.get(run['id'])['stale'] is True


def test_report_limit_never_publishes_counts_without_retrievable_findings(database):
    from app.services.analysis import AnalysisService
    class TwoThemes(Model):
        def extract(self, product, reviews, context):
            return [FindingDraft(issue_type='reported_defect',theme=theme,description='Reported issue',
                    evidence=[Evidence(review_id=reviews[0]['_id'],quote='The hinge broke.')]) for theme in ['hinge','frame']]
    review(database)
    bounded=AnalysisService(database,TwoThemes(),NoMemory(),clock=lambda:NOW,max_findings=1)
    run=enqueue(bounded); work(bounded,database)
    response=bounded.get(run['id'])
    assert response['error_code']=='analysis_output_limit_exceeded'
    assert response['summary'] is None and response['supporting_review_counts'] is None
    with pytest.raises(ServiceError): bounded.findings('P',run['id'])
    allowed=AnalysisService(database,TwoThemes(),NoMemory(),clock=lambda:NOW,max_findings=2)
    published=enqueue(allowed); work(allowed,database)
    result=allowed.get(published['id'])
    assert result['error_code'] is None
    ids={finding['id'] for finding in allowed.findings('P',published['id'])['items']}
    assert len(ids)==2
    assert {count['finding_id'] for count in result['supporting_review_counts']}==ids


def test_missing_llm_returns_explicit_unavailable_instead_of_enqueuing(database):
    from app.services.analysis import AnalysisService
    svc=AnalysisService(database,None,None,clock=lambda:NOW)
    with pytest.raises(ServiceError) as error: enqueue(svc)
    assert error.value.code=='provider_not_configured'
    assert error.value.status_code==503


def test_rate_limit_resumes_validated_chunks_without_reissuing_prefix(database):
    from app.integrations.memory import ProviderError
    for i in range(11): review(database, f'r{i:02}')
    class Limited(Model):
        def __init__(self): super().__init__(); self.limited = False; self.requested = []
        def extract(self, product, reviews, context):
            self.requested.append([r['_id'] for r in reviews])
            if reviews[0]['_id'] == 'r05' and not self.limited:
                self.limited = True
                raise ProviderError('model_rate_limited')
            return super().extract(product, reviews, context)
    model = Limited(); svc = service(database, model)
    run = enqueue(svc); jobs = JobRepository(database)
    worker = Worker(jobs, {'analysis_runs': svc.handle}, clock=lambda: NOW)
    assert worker.tick()
    assert svc.get(run['id'])['error_code'] == 'model_rate_limited'
    # New service object and worker model a process restart; only provider state is shared.
    resumed = service(database, model)
    Worker(jobs, {'analysis_runs': resumed.handle}, clock=lambda: NOW + timedelta(seconds=60)).tick()
    assert resumed.get(run['id'])['status'] == 'completed'
    assert model.requested.count(['r00', 'r01', 'r02', 'r03', 'r04']) == 1
    finding = resumed.findings('P', run['id'])['items'][0]
    assert finding['supporting_review_count'] == 11
    assert set(finding['review_ids']) == {f'r{i:02}' for i in range(11)}


def test_groq_paced_chunks_defer_without_consuming_attempts(database):
    for i in range(11): review(database, f'r{i:02}')
    import httpx, json
    from app.integrations.llm import GroqModel
    calls = []
    def provider(request):
        supplied = json.loads(json.loads(request.content)['messages'][1]['content'])['reviews']
        calls.append(supplied)
        finding = {'issue_type':'reported_defect', 'theme':'hinge', 'description':'Hinge reports',
                   'evidence':[{'review_id':r['_id'], 'quote':'The hinge broke.'} for r in supplied]}
        return httpx.Response(200, json={'choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps({'findings':[finding]})}}]})
    model = GroqModel('secret', transport=httpx.MockTransport(provider))
    svc = service(database, model); run = enqueue(svc); jobs = JobRepository(database)
    now = NOW
    worker = Worker(jobs, {'analysis_runs': svc.handle}, clock=lambda: now)
    assert worker.tick()
    assert len(calls) == 1
    assert database.analysis_runs.find_one({'_id': run['id']})['processing']['attempts'] == 0
    now += timedelta(seconds=59)
    assert not worker.tick()
    now += timedelta(seconds=1)
    assert worker.tick() and len(calls) == 2
    now += timedelta(seconds=60)
    assert worker.tick() and len(calls) == 3
    assert svc.get(run['id'])['status'] == 'completed'
    assert svc.findings('P', run['id'])['items'][0]['supporting_review_count'] == 11


def test_missing_extraction_checkpoint_fails_without_partial_publication(database):
    for i in range(6): review(database, f'r{i:02}')
    model = Model(); model.extraction_interval_seconds = 60
    svc = service(database, model); run = enqueue(svc); jobs = JobRepository(database, max_attempts=1)
    Worker(jobs, {'analysis_runs': svc.handle}, clock=lambda: NOW).tick()
    database.analysis_outputs.delete_many({'run_id': run['id']})
    Worker(jobs, {'analysis_runs': svc.handle}, clock=lambda: NOW + timedelta(seconds=60)).tick()
    assert svc.get(run['id'])['status'] == 'failed'
    assert svc.get(run['id'])['error_code'] == 'analysis_checkpoint_unavailable'
    assert len(model.calls) == 1


def test_demo_scope_freezes_first_five_reviews_and_counts_only_sample(database):
    for i in reversed(range(8)):
        review(database, f'r{i}')
    svc = service(database)
    run = enqueue(svc, sample_size=5)
    assert run['denominator'] == 5
    assert run['scope']['sample_size'] == 5
    saved = svc.repository.get(run['id'])
    assert saved['review_ids'] == [f'r{i}' for i in range(5)]
    assert not svc.get(run['id'])['stale']
    work(svc, database)
    result = svc.get(run['id'])
    assert result['status'] == 'completed'
    assert result['supporting_review_counts'][0]['supporting_review_count'] == 5
    assert enqueue(svc)['denominator'] == 8
    assert enqueue(svc, sample_size=5)['snapshot_hash'] == run['snapshot_hash']
