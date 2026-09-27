"""Importer rejects corrupted or held-out evidence before writes."""
import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.summaries.initialization import prepare_artifact, sample_digest


def bundle(n=2):
    reviews=[{'_id':f'r{i}','parent_asin':'P','title':'Works','text':'Works well.',
              'rating':5,'timestamp':'2022-01-01 00:00:00+00:00','batch':'A',
              'batch_id':'dataset:A','held_out':False} for i in range(n)]
    entry={'_id':'P','eligible_count':n,'sample_size':n,'review_ids':[r['_id'] for r in reviews],
           'sample_sha256':sample_digest(reviews)}
    manifest={'version':1,'seed':'fixed','created_at':'2026-09-27T00:00:00+00:00',
              'filter':{'batch_id':{'$in':['dataset:A','dataset:B']},'timestamp':{'$lte':'2022-10-27 01:13:13.233000+00:00'}},'products':[entry]}
    artifact={'product_id':'P','review_ids':entry['review_ids'],'sample_sha256':entry['sample_sha256'],
              'alias_to_review_id':{f'R{i+1:02d}':r['_id'] for i,r in enumerate(reviews)},
              'model':'local/model','artifact_sha256':'a'*64,'prompt_version':'p1','prompt_sha256':'b'*64,
              'created_at':'2026-09-27T00:00:00+00:00','status':'citation_checks_passed',
              'human_quality_review':'pending','narrative':'One reviewer says it works.',
              'summary':{'themes':[{'claim':'One reviewer says it works.','kind':'other','polarity':'positive',
                                   'evidence':[{'review_id':'R01','quote':'Works well.','quote_id':'R01-S1'}]}]}}
    return manifest,reviews,artifact


def prepare(m,r,a):
    return prepare_artifact(m,r,json.dumps(a).encode(),group='bulk')


def test_fewer_than_twenty_preserves_coverage_provenance_and_pending_status():
    m,r,a=bundle();p=prepare(m,r,a)
    assert p.candidate.coverage.historical_sample_count==2
    assert p.candidate.delta_review_ids==['r0','r1']
    assert p.candidate.themes[0].evidence[0].review_id=='r0'
    assert p.candidate.semantic_review.status=='pending'
    assert p.model_manifest['import_provenance']['original_artifact_sha256']==hashlib.sha256(json.dumps(a).encode()).hexdigest()


@pytest.mark.parametrize('mutation',['hash','heldout','product','quote','membership','too_few','alias','future'])
def test_corruption_rejected(mutation):
    m,r,a=bundle()
    if mutation=='hash':a['sample_sha256']='0'*64
    if mutation=='heldout':r[0]['held_out']=True
    if mutation=='product':r[0]['parent_asin']='other'
    if mutation=='quote':a['summary']['themes'][0]['evidence'][0]['quote']='Invented'
    if mutation=='membership':a['review_ids']=['r0','outside']
    if mutation=='too_few':m['products'][0]['eligible_count']=30
    if mutation=='alias':a['alias_to_review_id']['R01']='outside'
    if mutation=='future':r[0]['timestamp']='2023-01-01 00:00:00+00:00'
    with pytest.raises(ValueError):prepare(m,r,a)


def test_artifact_approval_does_not_grant_publication():
    m,r,a=bundle();a['human_quality_review']='approved';a['semantic_review']={'status':'approved'}
    assert prepare(m,r,a).candidate.semantic_review.status=='pending'


def test_identity_stable_on_retry_but_distinct_for_alternate_candidate():
    m,r,a=bundle();first=prepare(m,r,a);assert first.candidate.job_id==prepare(m,r,a).candidate.job_id
    b=copy.deepcopy(a);b['model']='local/another'
    assert first.candidate.job_id!=prepare(m,r,b).candidate.job_id


def test_stage_replays_without_publication_or_unsampled_admission():
    from app.summaries.initialization import stage_prepared
    class RecordingRepository:
        def __init__(self):self.jobs={};self.members=set()
        def admit_initial_reviews(self,product_id,reviews):self.members.update(r['_id'] for r in reviews)
        def stage_initial_draft(self,product_id,candidate,raw_artifact_bytes,model_manifest):
            self.jobs.setdefault(candidate.job_id, 'P:1');return self.jobs[candidate.job_id]
    m,r,a=bundle();p=prepare(m,r,a);repo=RecordingRepository()
    assert stage_prepared(p,repo)==stage_prepared(p,repo)=='P:1'
    assert len(repo.jobs)==1 and repo.members=={'r0','r1'}


def test_model_failure_prevents_any_write():
    m,r,a=bundle();a['status']='needs_review'
    with pytest.raises(ValueError):prepare(m,r,a)


@pytest.fixture
def live_repo():
    import os
    from uuid import uuid4
    from pymongo import MongoClient
    from app.migrations.v3 import migrate
    from app.summaries.repository import SummaryRepository
    uri=os.environ.get('TEST_MONGODB_URI')
    if not uri:pytest.skip('requires disposable TEST_MONGODB_URI')
    client=MongoClient(uri,tz_aware=True,serverSelectionTimeoutMS=2000)
    db=client['test_initialization_'+uuid4().hex]
    migrate(db,False)
    yield SummaryRepository(db)
    client.drop_database(db.name);client.close()


def test_real_repository_duplicate_drafts_and_membership_are_idempotent(live_repo):
    from app.summaries.initialization import stage_prepared
    m,r,a=bundle();p=prepare(m,r,a)
    live_repo.database.reviews.insert_many([dict(row,timestamp=datetime.fromisoformat(row['timestamp'])) for row in r])
    first=stage_prepared(p,live_repo);assert stage_prepared(p,live_repo)==first
    assert live_repo.versions.count_documents({})==1
    assert live_repo.inputs.count_documents({})==2
    assert live_repo.current('P').current is None
    assert live_repo.history('P',None,10).items==[]
    b=copy.deepcopy(a);b['model']='alternative'
    assert stage_prepared(prepare(m,r,b),live_repo)!=first
    assert live_repo.inputs.count_documents({})==2


def test_new_submission_during_initialization_remains_pending(live_repo):
    from app.summaries.initialization import stage_prepared
    m,r,a=bundle()
    live_repo.database.reviews.insert_many([dict(row,timestamp=datetime.fromisoformat(row['timestamp'])) for row in r])
    live_repo.admit_review({'_id':'new','parent_asin':'P','source':'user_submission','timestamp':datetime.now(timezone.utc)})
    stage_prepared(prepare(m,r,a),live_repo)
    assert live_repo.current('P').pending_review_count==1
    assert live_repo.inputs.find_one({'review_id':'new'})['incorporated_version'] is None


def test_database_source_drift_stops_before_write(live_repo):
    from app.summaries.initialization import verify_live_sources
    m,r,a=bundle();p=prepare(m,r,a)
    live_repo.database.products.insert_one({'_id':'P'})
    live_repo.database.reviews.insert_many(r)
    verify_live_sources([p],live_repo.database)
    live_repo.database.reviews.update_one({'_id':'r0'},{'$set':{'text':'changed'}})
    with pytest.raises(ValueError):verify_live_sources([p],live_repo.database)
    assert live_repo.versions.count_documents({})==0


def test_rejection_maps_original_review_to_normalized_digest(live_repo):
    from app.summaries.initialization import stage_prepared, apply_rejection
    m,r,a=bundle();p=prepare(m,r,a)
    live_repo.database.reviews.insert_many([dict(row,timestamp=datetime.fromisoformat(row['timestamp'])) for row in r])
    p.model_manifest['import_provenance']['semantic_rejection']={'status':'rejected','reviewer_id':'auditor','reviewer_type':'automated','reviewed_at':datetime.now(timezone.utc).isoformat(),'rubric_version':'v1','factual_support':False}
    version_id=stage_prepared(p,live_repo);apply_rejection(p,live_repo,version_id)
    saved=live_repo.draft(version_id)
    assert saved.semantic_review.status=='rejected'
    assert saved.semantic_review.coverage is None
    assert saved.semantic_review.artifact_sha256==hashlib.sha256(p.normalized_bytes).hexdigest()
    assert live_repo.current('P').current is None
