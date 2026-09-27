"""Real local Mongo boundary tests; regressions here lose or expose customer feedback."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
import os
import pytest
from pymongo import MongoClient
from fastapi.testclient import TestClient
from app.main import create_app
from app.domain import Principal, ReviewInput
from datetime import datetime, timedelta, timezone

@pytest.fixture
def database():
    client = MongoClient(os.getenv('TEST_MONGODB_URI', 'mongodb://127.0.0.1:27029'), tz_aware=True, serverSelectionTimeoutMS=2000)
    name = 'test_task2_' + uuid4().hex
    db = client[name]
    yield db
    client.drop_database(name)
    client.close()

@pytest.fixture
def service(database):
    from app.migrations.v2 import migrate
    from app.services.reviews import ReviewService
    migrate(database, False)
    database.products.insert_one({'_id':'P','title':'Product','provenance':{},'asins':['V']})
    return ReviewService(database)

@pytest.fixture
def api(service, settings):
    return TestClient(create_app(settings, SimpleNamespace(reviews=service)))

HEADERS={'Authorization':'Bearer reviewer-secret-value','Idempotency-Key':'key'}
PAYLOAD={'title':'Problem','text':'The hinge broke','rating':2}

def test_submission_replay_conflict_and_owner(api, database):
    first=api.post('/api/v1/products/P/reviews',headers=HEADERS,json=PAYLOAD)
    assert first.status_code==201
    review_id=first.json()['id']
    assert api.post('/api/v1/products/P/reviews',headers=HEADERS,json=PAYLOAD).json()['id']==review_id
    assert database.reviews.count_documents({})==1
    assert api.post('/api/v1/products/P/reviews',headers=HEADERS,json={**PAYLOAD,'rating':3}).status_code==409
    assert api.post('/api/v1/products/other/reviews',headers=HEADERS,json=PAYLOAD).status_code==409
    assert api.get(f'/api/v1/reviews/{review_id}/status',headers=HEADERS).status_code==200
    database.reviews.update_one({'_id':review_id},{'$set':{'author_id':'other'}})
    assert api.get(f'/api/v1/reviews/{review_id}/status',headers=HEADERS).status_code==403

@pytest.mark.parametrize('patch',[{'rating':0},{'rating':2.5},{'rating':True},{'title':'x'*201},{'text':' '},{'text':'x'*10001},{'asin':'unknown'}])
def test_invalid_inputs(api,patch):
    assert api.post('/api/v1/products/P/reviews',headers=HEADERS,json={**PAYLOAD,**patch}).status_code==422

def test_missing_and_role(api):
    assert api.post('/api/v1/products/unknown/reviews',headers=HEADERS,json=PAYLOAD).status_code==404
    assert api.post('/api/v1/products/P/reviews',headers={**HEADERS,'Authorization':'Bearer pm-secret-value'},json=PAYLOAD).status_code==403
    assert api.get('/api/v1/products',headers=HEADERS).json()['items'][0]['id']=='P'

def test_review_batch_metadata_is_product_scoped_and_excludes_held_out(api, database):
    now=datetime.now(timezone.utc)
    def batch(identifier, label, counts):
        return {'_id':identifier,'dataset_id':'dataset','label':label,'held_out':label=='C',
                'start_at':now-timedelta(days=1),'end_at':now,'review_count':sum(counts.values()),
                'product_counts':counts}
    database.batches.insert_many([batch('dataset:A','A',{'P':1000,'other':4}),
                                  batch('dataset:B','B',{'other':1000}),
                                  batch('dataset:C','C',{'P':100})])
    path='/api/v1/products/P/review-batches'
    pm={'Authorization':'Bearer pm-secret-value'}
    response=api.get(path,headers=pm)
    assert response.status_code==200
    assert response.json()=={'items':[{'id':'dataset:A','label':'A','review_count':1000}]}
    assert api.get(path,headers=HEADERS).status_code==403
    assert api.get('/api/v1/products/missing/review-batches',headers=pm).status_code==404

def test_concurrent_duplicate_is_one_durable_review(service,database):
    def submit(_):
        return service.submit('P',Principal(user_id='same',role='reviewer'),'retry',ReviewInput(**PAYLOAD))['id']
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids=list(pool.map(submit,range(8)))
    assert len(set(ids))==1
    assert database.reviews.count_documents({})==1
    assert database.reviews.find_one({})['processing']['status']=='pending'

def test_tied_timestamp_pagination_and_visibility(service,database):
    principal=Principal(user_id='owner',role='reviewer')
    for n in range(5): service.submit('P',principal,str(n),ReviewInput(**PAYLOAD))
    stamp=database.reviews.find_one({})['timestamp']
    database.reviews.update_many({}, {'$set':{'timestamp':stamp}})
    ids=[]; cursor=None
    while True:
        page=service.list('P',principal,None,None,cursor,2)
        ids.extend(r['id'] for r in page['items']); cursor=page['next_cursor']
        if not cursor: break
    assert len(ids)==len(set(ids))==5
    assert service.list('P',Principal(user_id='other',role='reviewer'),None,None,None,20)['items']==[]

def test_persisted_limit_and_replay(service,database):
    principal=Principal(user_id='owner',role='reviewer')
    from app.errors import ServiceError
    for n in range(10): service.submit('P',principal,str(n),ReviewInput(**PAYLOAD))
    assert service.submit('P',principal,'0',ReviewInput(**PAYLOAD))['id']
    with pytest.raises(ServiceError) as error: service.submit('P',principal,'over',ReviewInput(**PAYLOAD))
    assert error.value.status_code==429


def test_cursor_rejects_invalid_and_non_string_values(service):
    import base64
    import json
    from app.errors import ServiceError
    principal=Principal(user_id='owner',role='reviewer')
    for cursor in ('nonsense',base64.urlsafe_b64encode(json.dumps([123,'id']).encode()).decode()):
        with pytest.raises(ServiceError) as error:
            service.list('P',principal,None,None,cursor,20)
        assert error.value.status_code==422


def test_status_and_list_hide_internal_identity_and_lease(api, database):
    response=api.post('/api/v1/products/P/reviews',headers=HEADERS,json=PAYLOAD)
    review_id=response.json()['id']
    database.reviews.update_one({'_id':review_id},{'$set':{'processing.owner_token':'private-lease-token'}})
    pm={'Authorization':'Bearer pm-secret-value'}
    for path in (f'/api/v1/reviews/{review_id}/status','/api/v1/products/P/reviews'):
        response=api.get(path,headers=pm)
        assert response.status_code==200
        assert 'private-lease-token' not in response.text
        assert 'author_id' not in response.text


def test_valid_variant_and_default_parent_identity(api,database):
    assert api.post('/api/v1/products/P/reviews',headers=HEADERS,json={**PAYLOAD,'asin':'V'}).status_code==201
    assert database.reviews.find_one({})['asin']=='V'
    second=api.post('/api/v1/products/P/reviews',headers={**HEADERS,'Idempotency-Key':'second'},json=PAYLOAD)
    assert database.reviews.find_one({'_id':second.json()['id']})['asin']=='P'


def test_pm_priority_feed_includes_unsummarized_submissions_and_pages_stably(service,database):
    pm=Principal(user_id='pm',role='pm')
    stamp=datetime(2026,1,1,tzinfo=timezone.utc)
    for identifier,rating,source,minutes in [
        ('old-negative',1,'amazon_2023',4),('old-negative-z',2,'amazon_2023',4),
        ('new-negative',2,'user_submission',1),
        ('new-neutral',3,'user_submission',2),('old-positive',5,'amazon_2023',5),
        ('new-positive',4,'user_submission',3),('old-neutral',3,'amazon_2023',6)]:
        document={'_id':identifier,'parent_asin':'P','asin':'P',
            'title':identifier,'text':'Actual feedback '+identifier,'rating':rating,
            'timestamp':stamp+timedelta(minutes=minutes),
            'timestamp_ms':int((stamp+timedelta(minutes=minutes)).timestamp()*1000),
            'provenance':{'kind':'test'}}
        if source=='user_submission':
            document.update(source=source,created_at=stamp,author_id='private-owner',
                version=1,processing={'status':'pending','attempts':0},
                idempotency_key=identifier,payload_digest='test-digest')
        else:
            document.update(source=source,batch='A',batch_id='test:A',held_out=False,dataset_id='test')
        database.reviews.insert_one(document)
    seen=[]; cursor=None
    while True:
        page=service.list('P',pm,None,None,cursor,2,sort='priority')
        seen.extend(item['id'] for item in page['items'])
        assert all('author_id' not in item for item in page['items'])
        cursor=page['next_cursor']
        if cursor is None: break
    assert seen==['new-negative','new-neutral','new-positive','old-negative-z','old-negative','old-neutral','old-positive']
    assert service.list('P',pm,None,None,None,10,sort='newest')['items'][0]['id']=='old-neutral'
    assert [r['id'] for r in service.list('P',pm,None,None,None,10,sentiment='negative')['items']]==['new-negative','old-negative-z','old-negative']
    assert [r['id'] for r in service.list('P',pm,None,None,None,10,rating=4)['items']]==['new-positive']
    assert service.list('P',pm,None,None,None,10,sentiment='positive',rating=1)['items']==[]
    assert [r['id'] for r in service.list('P',pm,None,None,None,10,sentiment='positive',rating=4)['items']]==['new-positive']
    cursor=service.list('P',pm,None,None,None,1,sort='priority')['next_cursor']
    from app.errors import ServiceError
    with pytest.raises(ServiceError) as error:
        service.list('P',pm,None,None,cursor,1,sort='newest')
    assert error.value.status_code==422


def test_review_feed_api_rejects_bad_filters_and_cursor_scope(api,database):
    pm={'Authorization':'Bearer pm-secret-value'}
    for path in ('?sentiment=bad','?rating=0','?rating=6','?sort=oldest'):
        assert api.get('/api/v1/products/P/reviews'+path,headers=pm).status_code==422
    stamp=datetime.now(timezone.utc)
    database.reviews.insert_one({'_id':'r1','parent_asin':'P','asin':'P','title':'Issue',
        'text':'It failed','rating':1,'timestamp':stamp,'timestamp_ms':int(stamp.timestamp()*1000),
        'source':'user_submission','author_id':'private-owner','created_at':stamp,
        'version':1,'provenance':{'kind':'test'},'processing':{'status':'pending','attempts':0},
        'idempotency_key':'r1','payload_digest':'test-digest'})
    page=api.get('/api/v1/products/P/reviews?sort=priority&limit=1',headers=pm)
    assert page.status_code==200
    assert page.json()['items'][0]['id']=='r1'
    assert 'author_id' not in page.text
