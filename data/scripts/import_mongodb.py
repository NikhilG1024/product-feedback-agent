"""Non-destructive, idempotent import. Logs never include MongoDB URI or raw exceptions."""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from pipeline import DATASET, canonical, validate_dataset
from mongo_contract import SCHEMAS, INDEXES, DATE_FIELDS, V2_SCHEMAS, IDEMPOTENCY_INDEX

ROOT=Path(__file__).resolve().parents[2]

class Conflict(RuntimeError):pass

def documents(path):
    result={name:[] for name in SCHEMAS}
    for name in ('products','reviews','batches'):
        result[name]=[json.loads(line) for line in (path/(name+'.jsonl')).read_text().splitlines()]
        ids=[r['_id'] for r in result[name]]
        if len(ids)!=len(set(ids)):raise Conflict('Duplicate local identities: '+name)
    validate_dataset(result['products'],result['reviews'],result['batches'])
    for name,rows in result.items():
        for row in rows:
            for field in DATE_FIELDS.get(name,[]):
                row[field]=datetime.fromisoformat(row[field].replace('Z','+00:00'))
    return result

def check_document(existing, wanted, collection):
    if existing is not None and existing != wanted:
        raise Conflict('Existing record differs; no replacement allowed in '+collection)

def preflight(db, docs):
    existing={c['name']:c for c in db.list_collections()}
    for name, wanted_rows in docs.items():
        if name not in existing:continue
        options=existing[name].get('options',{})
        if existing[name].get('type')!='collection' or options.get('validator') not in (SCHEMAS[name], V2_SCHEMAS[name]) or options.get('validationAction','error')!='error' or options.get('validationLevel','strict')!='strict':
            raise Conflict('Existing collection has incompatible/unmanaged validator: '+name)
        if db[name].count_documents({'$nor':[options.get('validator')]},limit=1):
            raise Conflict('Existing records fail expected schema: '+name)
        indexes=list(db[name].list_indexes())
        for index in indexes:
            if index.get('unique') and index['name']!='_id_' and not (name=='reviews' and index['name']==IDEMPOTENCY_INDEX['name'] and list(index['key'].items())==IDEMPOTENCY_INDEX['keys'] and index.get('partialFilterExpression')==IDEMPOTENCY_INDEX['partialFilterExpression']):
                raise Conflict('Unexpected unique index requires manual review: '+name)
        for index_name, keys in INDEXES[name]:
            for index in indexes:
                if index['name']==index_name and (list(index['key'].items())!=keys or index.get('unique') or index.get('partialFilterExpression') or index.get('sparse')):
                    raise Conflict('Index name/options conflict: '+name+'/'+index_name)
                if list(index['key'].items())==keys and index['name']!=index_name:
                    raise Conflict('Equivalent index has another name; review inventory: '+name)
        for row in wanted_rows:
            check_document(db[name].find_one({'_id':row['_id']}),row,name)
        # Detect same natural review/product identity assigned a different _id.
        if name=='products':
            for row in wanted_rows:
                if db[name].find_one({'parent_asin':row['_id'],'_id':{'$ne':row['_id']}}):
                    raise Conflict('Product identity exists under another ID')
        if name=='reviews':
            for row in wanted_rows:
                natural={k:row[k] for k in ('parent_asin','asin','timestamp_ms','rating','title','text')}
                natural['_id']={'$ne':row['_id']}
                if db[name].find_one(natural):raise Conflict('Review content exists under another ID')

def import_once(db,docs):
    from pymongo import UpdateOne
    count=0
    for name,rows in docs.items():
        if rows:
            result=db[name].bulk_write([UpdateOne({'_id':r['_id']},{'$setOnInsert':r},upsert=True) for r in rows],ordered=True)
            count+=result.upserted_count
    return count

def verify(db,docs):
    for name,rows in docs.items():
        for row in rows:check_document(db[name].find_one({'_id':row['_id']}),row,name)
        for row in rows:
            if db[name].find_one({'_id':row['_id']}) is None:raise Conflict('Missing persisted record: '+name)
    reviews=list(db.reviews.find({'dataset_id':DATASET}))
    if len(reviews)!=len(docs['reviews']):raise Conflict('Persisted dataset review count differs')
    for row in reviews:
        if db.products.find_one({'_id':row['parent_asin']}) is None or db.batches.find_one({'_id':row['batch_id']}) is None:
            raise Conflict('Broken review reference')
    for left,right in [('A','B'),('B','C')]:
        if max(r['timestamp'] for r in reviews if r['batch']==left)>=min(r['timestamp'] for r in reviews if r['batch']==right):
            raise Conflict('Chronological boundary overlap')
    counts={p:{b:sum(r['parent_asin']==p and r['batch']==b for r in reviews) for b in 'ABC'} for p in sorted({r['parent_asin'] for r in reviews})}
    return counts

STORAGE_CEILING_BYTES=400_000_000
MIN_IMPORT_RESERVE_BYTES=8_000_000

def storage_usage(client):
    # Require full user-database visibility; do not silently treat inaccessible databases as empty.
    inventory=client.admin.command({'listDatabases':1,'nameOnly':False,'authorizedDatabases':False})
    details={}
    for item in inventory['databases']:
        name=item['name']
        if name in ('admin','local','config'):continue
        stats=client[name].command('dbStats',scale=1)
        details[name]={'data_bytes':stats['dataSize'],'storage_bytes':stats['storageSize'],'index_bytes':stats['indexSize']}
    return {'scope':'all non-system databases','databases':details,
            'metric':'uncompressed dataSize + indexSize (Atlas Free quota)',
            'total_bytes':sum(v['data_bytes']+v['index_bytes'] for v in details.values())}

def enforce_capacity(used, reserve):
    if used+reserve>STORAGE_CEILING_BYTES:
        raise Conflict('Storage ceiling: existing data plus conservative import reserve exceeds 400 MB')

def run(args):
    from dotenv import load_dotenv
    from pymongo import MongoClient
    load_dotenv(ROOT/'.env',override=False)
    uri=os.getenv('MONGODB_URI');database=os.getenv('MONGODB_DATABASE')
    if not uri or not database:raise Conflict('MONGODB_URI and MONGODB_DATABASE must be configured locally in ignored .env')
    if database in ('admin','config','local'):raise Conflict('Use a dedicated application database')
    docs=documents(args.data)
    with MongoClient(uri,serverSelectionTimeoutMS=15000,tz_aware=True) as client:
        client.admin.command('ping');db=client[database]
        preflight(db,docs)
        from bson import BSON
        proposed_bytes=sum(len(BSON.encode(row)) for rows in docs.values() for row in rows)
        storage_before=storage_usage(client)
        reserve=max(MIN_IMPORT_RESERVE_BYTES,proposed_bytes*4)
        enforce_capacity(storage_before['total_bytes'],reserve)
        if args.preflight_only:
            print(json.dumps({'database':database,'preflight':'passed','writes':0,'storage_before':storage_before,'import_reserve_bytes':reserve}));return
        existing=set(db.list_collection_names())
        created=[]
        for name in SCHEMAS:
            if name not in existing:
                db.create_collection(name,validator=SCHEMAS[name],validationLevel='strict',validationAction='error');created.append(name)
        for name,indices in INDEXES.items():
            for index_name,keys in indices:db[name].create_index(keys,name=index_name)
        before={n:db[n].count_documents({}) for n in SCHEMAS}
        inserted=import_once(db,docs);counts=verify(db,docs)
        after={n:db[n].count_documents({}) for n in SCHEMAS}
        repeated=import_once(db,docs);verify(db,docs)
        if repeated or after!={n:db[n].count_documents({}) for n in SCHEMAS}:raise Conflict('Repeated import changed counts')
        plans={}
        product=docs['products'][0]['_id']
        queries={'product_time':{'parent_asin':product},'product_batch_time':{'parent_asin':product,'batch_id':DATASET+':A'}}
        for name,query in queries.items():
            command={'find':'reviews','filter':query,'sort':{'timestamp':1}}
            plans[name]={'unhinted':db.command('explain',command,verbosity='executionStats'),
                         'hinted':db.command('explain',dict(command,hint=name),verbosity='executionStats')}
        try:stats=db.command('dbStats',scale=1)
        except Exception:stats={'unavailable':True}
        storage_after=storage_usage(client)
        enforce_capacity(storage_after['total_bytes'],0)
        report=dict(status='verified',database=database,created_collections=created,counts_before=before,counts_after=after,
                    first_import_inserted=inserted,second_import_inserted=repeated,counts_by_product_batch=counts,
                    storage_ceiling_bytes=STORAGE_CEILING_BYTES,storage_before=storage_before,storage_after=storage_after,import_reserve_bytes=reserve,
                    indexes={n:[dict(x) for x in db[n].list_indexes()] for n in SCHEMAS},storage=stats,
                    query_plans=plans,representative_review_ids=[db.reviews.find_one(q,{'_id':1})['_id'] for q in queries.values()])
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2,default=str))
        print(json.dumps({k:report[k] for k in ('status','database','counts_after','second_import_inserted')}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=ROOT/'data/processed')
    parser.add_argument('--report',type=Path,default=ROOT/'data/processed/mongodb-validation.json')
    parser.add_argument('--preflight-only',action='store_true');args=parser.parse_args()
    try:run(args)
    except Conflict as exc:parser.exit(2,'Import stopped: '+str(exc)+'\n')
    except Exception as exc:parser.exit(2,'Import stopped ('+type(exc).__name__+'). Raw error omitted to protect credentials; verify access/configuration locally.\n')
