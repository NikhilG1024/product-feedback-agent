import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from pymongo import MongoClient
from app.domain import DecisionInput, Scope, Principal
from app.errors import ServiceError

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
PM = Principal(user_id='pm', role='pm')

@pytest.fixture
def database():
    client = MongoClient(os.getenv('TEST_MONGODB_URI', 'mongodb://127.0.0.1:27029'), tz_aware=True)
    name = 'test_task6_' + uuid4().hex
    db = client[name]
    from app.migrations.v2 import migrate
    migrate(db, False)
    db.products.insert_one({'_id':'P', 'title':'Chair', 'provenance':{}})
    yield db
    client.drop_database(name)
    client.close()


def test_decision_server_time_prevents_historical_backdating(database):
    from app.services.decisions import DecisionService
    service = DecisionService(database, None, clock=lambda: NOW)
    saved = service.create('P', PM, DecisionInput(kind='correction', rationale='Hinge stiffness is a preference.', evidence_ids=[], available_through=NOW-timedelta(days=30)))
    assert saved['decided_at'] == NOW
    assert saved['processing']['memory_status'] == 'pending'
    assert service.eligible('P', Scope(source='user_submission'), NOW-timedelta(days=1)) == []
    assert service.eligible('P', Scope(source='user_submission'), NOW)[0]['_id'] == saved['id']
    with pytest.raises(ServiceError) as error:
        service.create('P', PM, DecisionInput(kind='correction', rationale='Bad evidence', evidence_ids=['foreign']))
    assert error.value.code == 'invalid_decision_evidence'


def test_decision_routes_and_confirmed_memory(database, settings):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.decisions import DecisionService
    from app.worker import Worker
    from app.repositories.jobs import JobRepository
    from test_review_processing import Memory
    memory = Memory()
    service = DecisionService(database, memory, clock=lambda:NOW)
    client = TestClient(create_app(settings, SimpleNamespace(decisions=service)))
    route = '/api/v1/products/P/decisions'
    headers = {'Authorization':'Bearer pm-secret-value'}
    response = client.post(route, headers=headers, json={'kind':'correction','rationale':'Consider stiffness a preference.','evidence_ids':[]})
    assert response.status_code == 201
    worker = Worker(JobRepository(database), {'decisions':service.handle}, clock=lambda:NOW)
    worker.tick()
    assert client.get(route, headers=headers).json()['items'][0]['processing']['memory_status']=='pending'
    memory.pending = False
    worker.clock=lambda:NOW+timedelta(seconds=6)
    worker.tick()
    record=client.get(route,headers=headers).json()['items'][0]
    assert record['processing']['memory_status']=='synced'
    assert record['processing']['status']=='completed'
    assert 'author_id' not in record and 'checkpoints' not in record['processing']
    assert client.post(route,headers={'Authorization':'Bearer reviewer-secret-value'},json={'kind':'decision','rationale':'No','evidence_ids':[]}).status_code==403


@pytest.mark.parametrize('changes', [{'parent_asin':'OTHER'}, {'timestamp':NOW+timedelta(seconds=1)}, {'available_at':NOW+timedelta(seconds=1)}])
def test_rejects_foreign_or_unavailable_evidence(database, changes):
    from app.services.decisions import DecisionService
    review={'_id':'r','parent_asin':'P','asin':'P','title':'Chair','text':'Hinge','rating':2,'timestamp':NOW,'timestamp_ms':1,'batch':'A','batch_id':'batch-A','held_out':False,'dataset_id':'dataset','provenance':{}}
    review.update(changes)
    database.reviews.insert_one(review)
    with pytest.raises(ServiceError) as error:
        DecisionService(database, clock=lambda:NOW).create('P',PM,DecisionInput(kind='correction',rationale='Reason',evidence_ids=['r']))
    assert error.value.code=='invalid_decision_evidence'


@pytest.mark.parametrize('payload', [dict(kind='unknown',rationale='Reason'),dict(kind='correction',rationale=' '),dict(kind='decision',rationale='Reason',available_through=NOW+timedelta(days=1))])
def test_invalid_decision_rejected(database,payload):
    from app.services.decisions import DecisionService
    with pytest.raises(ServiceError): DecisionService(database,clock=lambda:NOW).create('P',PM,DecisionInput(evidence_ids=[],**payload))


def test_guidance_checks_only_referenced_evidence_in_exact_scope(database):
    from app.services.decisions import DecisionService
    from app.repositories.analysis import AnalysisRepository
    for key, changes in [('eligible',{}),('held',{'held_out':True,'batch':'C'}),('batch-b',{'batch_id':'batch-B'}),('foreign',{'parent_asin':'OTHER'}),('future',{'available_at':NOW+timedelta(seconds=1)})]:
        row={'_id':key,'parent_asin':'P','asin':'P','title':'Chair','text':'Hinge','rating':2,'timestamp':NOW-timedelta(days=1),'timestamp_ms':1,'batch':'A','batch_id':'batch-A','held_out':False,'dataset_id':'dataset','provenance':{}}
        row.update(changes); database.reviews.insert_one(row)
        database.decisions.insert_one({'_id':'d-'+key,'parent_asin':'P','kind':'correction','rationale':'Guidance','evidence_ids':[key],'decided_at':NOW,'available_through':NOW})
    scope=Scope(source='amazon_2023',batch_id='batch-A')
    service=DecisionService(database,clock=lambda:NOW)
    assert [d['_id'] for d in service.eligible('P',scope,NOW)]==['d-eligible']
    assert service.eligible('P',Scope(source='user_submission'),NOW)==[]
    assert [d['_id'] for d in AnalysisRepository(database).eligible_decisions('P',NOW,['eligible'])]==['d-eligible']


@pytest.mark.parametrize('include_eligible', [False, True])
def test_out_of_scope_decisions_do_not_consume_guidance_budget(database, include_eligible):
    from app.services.decisions import DecisionService
    from test_review_processing import submit
    live=submit(database)
    database.reviews.insert_one({'_id':'imported-review','parent_asin':'P','asin':'P','title':'Chair','text':'Hinge','rating':2,'timestamp':NOW-timedelta(days=2),'timestamp_ms':1,'batch':'A','batch_id':'batch-A','held_out':False,'dataset_id':'dataset','provenance':{}})
    database.decisions.insert_many([{'_id':f'historical-{i:03}','parent_asin':'P','kind':'correction','rationale':'Imported-only guidance',
        'evidence_ids':['imported-review'],'decided_at':NOW-timedelta(days=1),'available_through':NOW-timedelta(days=1)} for i in range(101)])
    if include_eligible:
        database.decisions.insert_one({'_id':'live-guidance','parent_asin':'P','kind':'correction','rationale':'Live guidance',
            'evidence_ids':[live['id']],'decided_at':NOW,'available_through':NOW})
    from datetime import datetime, timezone
    result=DecisionService(database).eligible('P',Scope(source='user_submission'),datetime.now(timezone.utc))
    assert [d['_id'] for d in result] == (['live-guidance'] if include_eligible else [])


def test_more_than_one_hundred_truly_eligible_decisions_is_explicit_error(database):
    from app.services.decisions import DecisionService
    database.decisions.insert_many([{'_id':f'guidance-{i:03}','parent_asin':'P','kind':'correction','rationale':'General guidance',
        'evidence_ids':[],'decided_at':NOW,'available_through':NOW} for i in range(101)])
    with pytest.raises(ServiceError) as error:
        DecisionService(database).eligible('P',Scope(source='user_submission'),NOW)
    assert error.value.code=='narrower_guidance_scope_required'
