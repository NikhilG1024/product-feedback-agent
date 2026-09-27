from datetime import timedelta
from app.domain import ReviewInput, Principal, DecisionInput, FindingDraft, Evidence, MemoryContext
from app.integrations.memory import RetentionPending, ProviderError
from app.repositories.jobs import JobRepository
from app.services.reviews import ReviewService
from app.worker import Worker
from test_decisions import database, NOW, PM

REVIEWER = Principal(user_id='reviewer', role='reviewer')

class Memory:
    def __init__(self): self.records = {}; self.states = []; self.pending = True
    def ensure_retained(self, bank, record, *, state, checkpoint):
        self.states.append(state)
        checkpoint({'operation_id': record.id, 'status': 'pending' if self.pending else 'completed'})
        if self.pending: raise RetentionPending()
        self.records.setdefault(bank, {})[record.id] = record
    def recall(self, bank, query):
        values = self.records[bank]
        return MemoryContext(text='\n'.join(r.content for r in values.values()), record_ids=list(values))

class Model:
    def __init__(self): self.contexts = []
    def extract(self, product, reviews, context):
        self.contexts.append(context)
        return [FindingDraft(issue_type='preference' if 'stiffness' in context.text else 'reported_defect', theme='hinge', description='Provisional', evidence=[Evidence(review_id=reviews[0]['_id'], quote='The hinge broke.')])]


def submit(db, key='one'):
    return ReviewService(db).submit('P', REVIEWER, key, ReviewInput(title='Chair', text='The hinge broke.', rating=2))


def test_saved_review_pending_retention_then_confirmed_classification(database):
    from app.services.review_processing import ReviewProcessor
    saved = submit(database)
    memory, model = Memory(), Model()
    processor = ReviewProcessor(database, model, memory)
    worker = Worker(JobRepository(database), {'reviews': processor.handle})
    assert worker.tick()
    status = ReviewService(database).status(saved['id'], REVIEWER)['processing']
    assert status['memory_status'] == 'pending'
    assert status['classification_status'] == 'pending'
    memory.pending = False
    worker.clock = lambda: processor.clock()+timedelta(seconds=6)
    assert worker.tick()
    status = ReviewService(database).status(saved['id'], REVIEWER)['processing']
    assert status['memory_status'] == 'synced'
    assert status['classification_status'] == 'completed'
    assert status['status'] == 'completed'
    assert memory.states[1]['operation_id'] == 'review:'+saved['id']


def test_pm_correction_only_in_later_review_and_old_report_unchanged(database):
    from app.services.decisions import DecisionService
    from app.services.review_processing import ReviewProcessor
    from app.services.analysis import AnalysisService
    from app.domain import Scope, AnalysisInput
    from datetime import datetime, timezone
    memory, model = Memory(), Model()
    memory.pending = False
    first = submit(database)
    cutoff = datetime.now(timezone.utc)
    analysis = AnalysisService(database, model, memory, clock=lambda:cutoff)
    run = analysis.enqueue('P', PM, AnalysisInput(mode='memory', scope=Scope(source='user_submission')))
    Worker(JobRepository(database), {'analysis_runs':analysis.handle}).tick()
    original = analysis.findings('P',run['id'])
    correction = DecisionService(database, memory).create('P', PM, DecisionInput(kind='correction', rationale='Treat stiffness as preference.', evidence_ids=[first['id']]))
    second = submit(database, 'two')
    worker = Worker(JobRepository(database), {'reviews':ReviewProcessor(database, model, memory).handle})
    worker.tick(); worker.tick()
    reviews = ReviewService(database).list('P', PM, 'user_submission', None, None, 20)['items']
    by_id = {r['id']:r for r in reviews}
    assert by_id[first['id']]['guidance_references'] == []
    assert by_id[first['id']]['provisional_findings'][0]['issue_type'] == 'reported_defect'
    assert by_id[second['id']]['guidance_references'] == [correction['id']]
    assert by_id[second['id']]['provisional_findings'][0]['issue_type'] == 'preference'
    assert analysis.findings('P',run['id']) == original
    assert len(memory.records) == 3
    later_analysis = AnalysisService(database, model, memory)
    later = later_analysis.enqueue('P', PM, AnalysisInput(mode='memory', scope=Scope(source='user_submission')))
    Worker(JobRepository(database), {'analysis_runs':later_analysis.handle}).tick()
    assert later_analysis.get(later['id'])['guidance_references'][0]['decision_id'] == correction['id']
    assert analysis.findings('P',run['id']) == original
    assert all('author_id' not in record.metadata for bank in memory.records.values() for record in bank.values())


def test_outage_keeps_saved_review_and_no_fabricated_classification(database):
    from app.services.review_processing import ReviewProcessor
    class Outage(Memory):
        def ensure_retained(self,*args,**kwargs): raise ProviderError('memory_unavailable')
    saved=submit(database)
    Worker(JobRepository(database),{'reviews':ReviewProcessor(database,Model(),Outage()).handle}).tick()
    result=ReviewService(database).list('P',REVIEWER,None,None,None,20)['items'][0]
    assert result['id']==saved['id'] and result['text']=='The hinge broke.'
    assert result['processing']['memory_status']=='pending'
    assert result['processing']['attempts']==1
    assert result['provisional_findings'] is None


def test_lost_lease_stops_retention_and_foreign_recall_never_publishes(database):
    import pytest
    from threading import Event
    from datetime import datetime, timezone
    from app.worker import JobContext
    from app.services.review_processing import ReviewProcessor
    saved=submit(database)
    memory=Memory(); memory.pending=False
    processor=ReviewProcessor(database,Model(),memory)
    jobs=JobRepository(database); now=datetime.now(timezone.utc)
    claim=jobs.claim('reviews',now,1)
    with pytest.raises(ProviderError):
        processor.handle(jobs.get(claim),JobContext(jobs,claim,lambda:now+timedelta(seconds=2),Event(),Event()))
    assert memory.records=={}
    class Foreign(Memory):
        def recall(self,*args): return MemoryContext(text='Foreign',record_ids=['decision:foreign'])
    memory=Foreign(); memory.pending=False
    worker=Worker(jobs,{'reviews':ReviewProcessor(database,Model(),memory).handle},clock=lambda:now+timedelta(seconds=2))
    worker.tick()
    result=ReviewService(database).list('P',PM,None,None,None,20)['items'][0]
    assert result['provisional_findings'] is None
    assert result['processing']['memory_status']=='synced'
    assert result['processing']['classification_status']=='pending'


def test_configured_app_really_runs_review_and_decision_handlers(database, settings, monkeypatch):
    from app.main import configured_app
    from app.config import Settings
    import app.repositories.mongo as mongo
    from fastapi.testclient import TestClient
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls:settings))
    monkeypatch.setattr(mongo,'connect',lambda config:(database.client,database))
    app=configured_app()
    client=TestClient(app)
    reviewer={'Authorization':'Bearer reviewer-secret-value','Idempotency-Key':'configured'}
    response=client.post('/api/v1/products/P/reviews',headers=reviewer,json={'title':'Chair','text':'Hinge','rating':2})
    assert response.status_code==201
    pm={'Authorization':'Bearer pm-secret-value'}
    decision=client.post('/api/v1/products/P/decisions',headers=pm,json={'kind':'correction','rationale':'Preference','evidence_ids':[]})
    assert decision.status_code==201
    assert app.state.worker.tick()
    assert client.get('/api/v1/reviews/'+response.json()['id']+'/status',headers=reviewer).json()['processing']['attempts']==1
    assert app.state.worker.tick()
    assert client.get('/api/v1/products/P/decisions',headers=pm).json()['items'][0]['processing']['attempts']==1
    app.state.worker.stop()


def test_model_failure_preserves_sync_and_exposes_classification_failure(database, settings):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.review_processing import ReviewProcessor
    class BrokenModel(Model):
        def extract(self,*args): raise ProviderError('model_unavailable')
    saved=submit(database)
    memory=Memory(); memory.pending=False
    Worker(JobRepository(database,max_attempts=1),{'reviews':ReviewProcessor(database,BrokenModel(),memory).handle}).tick()
    client=TestClient(create_app(settings,SimpleNamespace(reviews=ReviewService(database))))
    result=client.get('/api/v1/reviews/'+saved['id']+'/status',headers={'Authorization':'Bearer pm-secret-value'}).json()['processing']
    assert result['memory_status']=='synced'
    assert result['classification_status']=='failed'
    assert result['error_code']=='provider_failed'


def test_successful_retry_clears_public_error(database, settings):
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.review_processing import ReviewProcessor
    class Recovering(Model):
        def extract(self,*args):
            if not self.contexts:
                self.contexts.append(None)
                raise ProviderError('model_unavailable')
            return super().extract(*args)
    saved=submit(database)
    memory=Memory(); memory.pending=False
    worker=Worker(JobRepository(database),{'reviews':ReviewProcessor(database,Recovering(),memory).handle})
    worker.tick()
    worker.clock=lambda:datetime.now(timezone.utc)+timedelta(seconds=3)
    worker.tick()
    client=TestClient(create_app(settings,SimpleNamespace(reviews=ReviewService(database))))
    result=client.get('/api/v1/reviews/'+saved['id']+'/status',headers={'Authorization':'Bearer pm-secret-value'}).json()['processing']
    assert result['status']=='completed'
    assert result['error_code'] is None


def test_memory_can_sync_when_model_credentials_are_missing(database):
    from app.services.review_processing import ReviewProcessor
    saved=submit(database)
    memory=Memory(); memory.pending=False
    Worker(JobRepository(database,max_attempts=1),{'reviews':ReviewProcessor(database,None,memory).handle}).tick()
    result=ReviewService(database).status(saved['id'],PM)['processing']
    assert result['memory_status']=='synced'
    assert result['classification_status']=='failed'


def test_incremental_review_is_not_limited_by_lifetime_review_count(database):
    from datetime import datetime, timezone
    from app.services.review_processing import ReviewProcessor
    now=datetime.now(timezone.utc)-timedelta(days=1)
    database.reviews.insert_many([{'_id':'old-'+str(i),'parent_asin':'P','asin':'P','source':'user_submission',
        'title':'Chair','text':'Old review','rating':3,'timestamp':now,'timestamp_ms':int(now.timestamp()*1000),
        'created_at':now,'author_id':'old','version':1,'provenance':{},'processing':{'status':'completed'},
        'idempotency_key':str(i),'payload_digest':'fixture'} for i in range(1501)])
    saved=submit(database)
    memory=Memory(); memory.pending=False
    Worker(JobRepository(database),{'reviews':ReviewProcessor(database,Model(),memory).handle}).tick()
    result=ReviewService(database).status(saved['id'],PM)['processing']
    assert result['status']=='completed'
    assert result['memory_status']=='synced'
