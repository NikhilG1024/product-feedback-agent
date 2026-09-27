from datetime import datetime, timezone
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'data/scripts'))
from mongo_contract import SCHEMAS
from tests.test_reviews import database


def legacy():
    return {'_id':'old','parent_asin':'P','asin':'V','title':'','text':'Original exact text','rating':4.0,'timestamp':datetime(2023,1,1,tzinfo=timezone.utc),'timestamp_ms':1672531200000,'batch':'A','batch_id':'batch-a','held_out':False,'dataset_id':'legacy-electronics-id','provenance':{}}

def test_migration_preserves_and_resumes(database):
    from app.migrations.v2 import migrate
    for name,schema in SCHEMAS.items(): database.create_collection(name,validator=schema)
    database.reviews.insert_one(legacy())
    before=database.reviews.find_one({})
    assert migrate(database,True)['dry_run'] is True
    assert database.reviews.find_one({})==before
    migrate(database,False); migrate(database,False)
    assert database.reviews.find_one({})==before
    from app.services.reviews import ReviewService
    from app.domain import Principal
    database.products.insert_one({'_id':'P','title':'P','provenance':{}})
    page=ReviewService(database).list('P',Principal(user_id='pm',role='pm'),'amazon_2023',None,None,20)
    assert [r['id'] for r in page['items']]==['old']
    from import_mongodb import preflight
    preflight(database,{'reviews':[legacy()]})

def test_unknown_validator_refused_before_any_changes(database):
    from app.migrations.v2 import migrate
    database.create_collection('reviews',validator={'text':{'$type':'string'}})
    with pytest.raises(ValueError,match='validator'): migrate(database,False)
    assert database.list_collection_names()==['reviews']


def test_partial_schema_progress_can_resume(database):
    from app.migrations.v2 import migrate
    from mongo_contract import V2_SCHEMAS
    for name,schema in SCHEMAS.items(): database.create_collection(name,validator=schema)
    database.command('collMod','reviews',validator=V2_SCHEMAS['reviews'])
    migrate(database,False)
    assert database.schema_migrations.find_one({'_id':'v2'})['version']==2
    assert database.reviews.index_information()['submission_author_key']['unique'] is True


def test_bad_existing_data_refused_without_mutation(database):
    from app.migrations.v2 import migrate
    database.create_collection('reviews',validator=SCHEMAS['reviews'])
    database.reviews.insert_one({'_id':'bad'},bypass_document_validation=True)
    with pytest.raises(ValueError,match='records'): migrate(database,False)
    assert database.list_collection_names()==['reviews']
