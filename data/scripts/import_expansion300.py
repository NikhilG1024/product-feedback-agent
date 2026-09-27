"""Transactional additive expansion, bounded by Atlas Free's uncompressed quota.
No existing reviews are replaced; new reviews and their batch counters commit together.
"""
import argparse,json,os
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path
from contextlib import contextmanager
from uuid import uuid4
from dotenv import load_dotenv
from pymongo import MongoClient,UpdateOne,ReturnDocument
from bson import BSON
from pipeline import DATASET,validate_dataset
from extract import ROOT
from import_mongodb import storage_usage,enforce_capacity,Conflict
from prepare_expansion300 import OUT,SOURCE_MANIFEST

NAMES=('products','reviews','batches','analysis_runs','findings','decisions')
CHUNK=1000

def load_documents(name):
 rows=[json.loads(line) for line in (OUT/(name+'.jsonl')).open()]
 for row in rows:
  for key in {'reviews':['timestamp'],'batches':['start_at','end_at']}.get(name,[]):row[key]=datetime.fromisoformat(row[key].replace('Z','+00:00'))
 return rows

def chunks(rows):
 for i in range(0,len(rows),CHUNK):yield rows[i:i+CHUNK]

def same(existing,wanted,name):
 if existing!=wanted:raise Conflict('Existing '+name+' record differs: '+wanted['_id'])

def emit(progress):
 temporary=OUT/'import-progress.tmp';temporary.write_text(json.dumps(progress,indent=2,default=str));temporary.replace(OUT/'import-progress.json');print(json.dumps(progress,default=str),flush=True)

def validate_input():
 import hashlib
 report=json.loads((OUT/'preparation-report.json').read_text())
 for filename,expected in report['files_sha256'].items():
  if hashlib.sha256((OUT/filename).read_bytes()).hexdigest()!=expected:raise Conflict('Prepared file hash changed')
 products=load_documents('products');reviews=load_documents('reviews');batches=load_documents('batches')
 if len(products)!=report['products'] or len(reviews)!=report['reviews'] or not 20<=len(products)<=300 or len(reviews)!=len(products)*1000:raise Conflict('Expanded stage size mismatch')
 return products,reviews,batches,report

@contextmanager
def ingestion_lease(db):
 owner=str(uuid4());batch_id=DATASET+':A';now=datetime.now(timezone.utc)
 leased=db.batches.find_one_and_update({'_id':batch_id,'$or':[{'ingestion_lease':{'$exists':False}},{'ingestion_lease.expires_at':{'$lt':now}}]},
  {'$set':{'ingestion_lease':{'owner':owner,'expires_at':now+timedelta(minutes=5)}}},return_document=ReturnDocument.AFTER)
 if leased is None:raise Conflict('Another ingestion holds the database lease')
 def renew(session=None):
  now=datetime.now(timezone.utc)
  result=db.batches.update_one({'_id':batch_id,'ingestion_lease.owner':owner,'ingestion_lease.expires_at':{'$gt':now}},{'$set':{'ingestion_lease.expires_at':now+timedelta(minutes=5)}},session=session)
  if result.matched_count!=1:raise Conflict('Ingestion lease was lost')
 try:yield renew
 finally:db.batches.update_one({'_id':batch_id,'ingestion_lease.owner':owner},{'$unset':{'ingestion_lease':''}})

def without_lease(row):
 return {k:v for k,v in row.items() if k!='ingestion_lease'}

def validate_existing_batches(db,batches):
 actual={b['_id']:{'count':0,'products':{},'start':None,'end':None} for b in batches}
 pipeline=[{'$match':{'dataset_id':DATASET}},{'$group':{'_id':{'batch':'$batch_id','product':'$parent_asin'},'count':{'$sum':1},'start':{'$min':'$timestamp'},'end':{'$max':'$timestamp'}}}]
 for stat in db.reviews.aggregate(pipeline):
  key=stat['_id']['batch']
  if key not in actual:raise Conflict('Unexpected existing batch reference')
  value=actual[key];value['count']+=stat['count'];value['products'][stat['_id']['product']]=stat['count']
  value['start']=min(value['start'],stat['start']) if value['start'] else stat['start']
  value['end']=max(value['end'],stat['end']) if value['end'] else stat['end']
 for batch in batches:
  row=db.batches.find_one({'_id':batch['_id']});a=actual[batch['_id']]
  if not row:raise Conflict('Missing existing batch')
  expected={'_id':batch['_id'],'dataset_id':DATASET,'label':batch['label'],'held_out':batch['held_out'],'review_count':a['count'],'product_counts':a['products'],'start_at':a['start'],'end_at':a['end']}
  if any(row.get(k)!=v for k,v in expected.items()):raise Conflict('Existing batch differs from persisted membership; no changes made')
  if row.get('source_manifest',SOURCE_MANIFEST)!=SOURCE_MANIFEST:raise Conflict('Conflicting source manifest; no changes made')
  allowed=set(expected)|{'source_manifest','ingestion_status','ingestion_lease'}
  if set(row)-allowed:raise Conflict('Existing batch has unmanaged fields; review before expansion')
  if row.get('ingestion_status','complete') not in ('running','complete'):raise Conflict('Incompatible ingestion status')

def prepare_target(db,products,batches):
 if set(NAMES)-set(db.list_collection_names()):raise Conflict('Run initial collection/schema setup first')
 batch_ids=[b['_id'] for b in batches]
 if db.analysis_runs.count_documents({'batch_id':{'$in':batch_ids}},limit=1):raise Conflict('Existing analysis uses these batches; freeze/version before changing their membership')
 # Review every current dataset record before mutations; nothing outside this cohort is deleted.
 validate_existing_batches(db,batches)
 original_products={r['_id']:r for r in load_documents_from_initial('products')}
 existing={p['_id']:p for p in db.products.find({'_id':{'$in':[p['_id'] for p in products]}})}
 for row in products:
  current=existing.get(row['_id'])
  if current is not None and current!=row and current!=original_products.get(row['_id']):raise Conflict('Conflicting existing product: '+row['_id'])
 for row in products:
  current=existing.get(row['_id'])
  if current is None:db.products.update_one({'_id':row['_id']},{'$setOnInsert':row},upsert=True)
  elif current!=row:
   result=db.products.update_one(dict(current,product_type={'$exists':False}),{'$set':{'product_type':row['product_type']}})
   if result.matched_count!=1:raise Conflict('Product changed concurrently')
 for batch in batches:
  current=db.batches.find_one({'_id':batch['_id']})
  if not current or current.get('label')!=batch['label'] or current.get('held_out')!=batch['held_out']:raise Conflict('Incompatible existing batch')
  db.batches.update_one({'_id':batch['_id']},{'$set':{'source_manifest':SOURCE_MANIFEST,'ingestion_status':'running'}})

def load_documents_from_initial(name):
 rows=[json.loads(line) for line in (ROOT/'data/processed'/(name+'.jsonl')).open()]
 return rows

def add_reviews(client,db,reviews,*,pass_number,renew):
 inserted_total=0;checked=0
 for group in chunks(reviews):
  renew()
  existing={r['_id']:r for r in db.reviews.find({'_id':{'$in':[r['_id'] for r in group]}})}
  for row in group:
   if row['_id'] in existing:same(existing[row['_id']],row,'review')
  missing=[r for r in group if r['_id'] not in existing]
  if missing:
   usage=storage_usage(client);incoming=sum(len(BSON.encode(r)) for r in missing)
   enforce_capacity(usage['total_bytes'],incoming+8_000_000+512*len(missing))
   # Callback is safe for PyMongo transaction retries; counters depend on actual upserted IDs.
   def insert_transaction(session):
    renew(session=session)
    result=db.reviews.bulk_write([UpdateOne({'_id':r['_id']},{'$setOnInsert':r},upsert=True) for r in missing],session=session,ordered=True)
    inserted=[missing[i] for i in result.upserted_ids]
    by_batch=defaultdict(list)
    for r in inserted:by_batch[r['batch_id']].append(r)
    for batch_id,rows in by_batch.items():
     increments={'review_count':len(rows)}
     increments.update({'product_counts.'+p:n for p,n in Counter(r['parent_asin'] for r in rows).items()})
     update={'$inc':increments,'$min':{'start_at':min(r['timestamp'] for r in rows)},'$max':{'end_at':max(r['timestamp'] for r in rows)}}
     result_batch=db.batches.update_one({'_id':batch_id},update,session=session)
     if result_batch.matched_count!=1:raise Conflict('Missing batch during transactional import')
    return len(inserted)
   with client.start_session() as session:inserted_total+=session.with_transaction(insert_transaction)
  checked+=len(group)
  if checked%5000==0 or checked==len(reviews):
   emit(dict(status='importing' if pass_number==1 else 'verifying_second_import',pass_number=pass_number,checked_reviews=checked,total_reviews=len(reviews),inserted_this_pass=inserted_total,quota=storage_usage(client)))
 return inserted_total

def verify(db,products,reviews,batches,renew):
 expected={r['_id']:r for r in reviews};seen=set();counts=Counter();batch_counts=defaultdict(Counter)
 dates=defaultdict(list)
 for row in db.reviews.find({'dataset_id':DATASET},batch_size=1000):
  if row['_id'] not in expected:raise Conflict('Unexpected record in imported dataset')
  same(row,expected[row['_id']],'review');seen.add(row['_id']);counts[row['parent_asin']]+=1;batch_counts[row['batch_id']][row['parent_asin']]+=1;dates[row['batch_id']].append(row['timestamp'])
  if len(seen)%5000==0:renew()
 if seen!=set(expected) or set(counts.values())!={1000} or len(counts)!=len(products):raise Conflict('Persisted count/identity verification failed')
 for row in products:same(db.products.find_one({'_id':row['_id']}),row,'product')
 for batch in batches:
  stored=db.batches.find_one({'_id':batch['_id']});wanted=dict(batch,ingestion_status='running')
  same(without_lease(stored),wanted,'batch')
 return {'review_count':len(seen),'product_count':len(counts),'counts_by_batch':{b:sum(c.values()) for b,c in batch_counts.items()},'reviews_per_product':1000,'full_document_readback':True,'references':True,'original_reviews_preserved':True}

def run():
 load_dotenv(ROOT/'.env');uri=os.getenv('MONGODB_URI');database=os.getenv('MONGODB_DATABASE')
 if not uri or not database:raise Conflict('Local MongoDB configuration missing')
 products,reviews,batches,prepared=validate_input()
 with MongoClient(uri,serverSelectionTimeoutMS=15000,tz_aware=True) as client:
  client.admin.command('ping');db=client[database]
  with ingestion_lease(db) as renew:
   before=storage_usage(client)
   expected_ids={r['_id'] for r in reviews}
   # All currently imported cohort records must be represented identically in the expanded bundle.
   incoming={r['_id']:r for r in reviews}
   existing_count=0;existing_bson=0
   for row in db.reviews.find({'dataset_id':DATASET},batch_size=1000):
    if row['_id'] not in incoming:raise Conflict('Expansion would omit an existing review')
    same(row,incoming[row['_id']],'review');existing_count+=1;existing_bson+=len(BSON.encode(row))
   # Conservative initial estimate; per-chunk guards additionally protect against actual index growth.
   incremental=max(0,prepared['bson_bytes']-existing_bson)
   enforce_capacity(before['total_bytes'],incremental+max(8_000_000,256*(len(reviews)-existing_count)))
   renew();prepare_target(db,products,batches)
   first=add_reviews(client,db,reviews,pass_number=1,renew=renew);checks=verify(db,products,reviews,batches,renew)
   second=add_reviews(client,db,reviews,pass_number=2,renew=renew)
   if second:raise Conflict('Second import inserted unexpected records')
   after=storage_usage(client);enforce_capacity(after['total_bytes'],0)
   for batch in batches:db.batches.update_one({'_id':batch['_id'],'ingestion_status':'running'},{'$set':{'ingestion_status':'complete'}})
   for batch in batches:same(without_lease(db.batches.find_one({'_id':batch['_id']})),batch,'batch')
   plans={name:db.command('explain',{'find':'reviews','filter':query,'sort':{'timestamp':1}},verbosity='executionStats') for name,query in {'product_time':{'parent_asin':products[0]['_id']},'product_batch_time':{'parent_asin':products[0]['_id'],'batch_id':DATASET+':A'}}.items()}
   report=dict(status='verified' if prepared['target_met'] else 'stage_verified',target_met=prepared['target_met'],target_products=300,target_reviews=300000,database=database,verified_at=datetime.now(timezone.utc).isoformat(),first_pass_inserted=first,second_pass_inserted=second,checks=checks,
    counts_by_type=dict(Counter(p['product_type'] for p in products)),counts={n:db[n].count_documents({}) for n in NAMES},quota_before=before,quota_after=after,
    indexes={n:[dict(i) for i in db[n].list_indexes()] for n in NAMES},query_plans=plans,storage_ceiling_bytes=400_000_000)
   (OUT/'mongodb-validation.json').write_text(json.dumps(report,indent=2,default=str));history=OUT/'stages';history.mkdir(exist_ok=True);(history/f'stage-{len(products):03d}-validation.json').write_text(json.dumps(report,indent=2,default=str));emit({k:report[k] for k in ['status','database','counts','quota_after','second_pass_inserted']})
if __name__=='__main__':
 try:run()
 except Conflict as exc:raise SystemExit('Expansion stopped: '+str(exc))
 except Exception as exc:raise SystemExit('Expansion stopped ('+type(exc).__name__+'); raw error omitted to protect credentials. Check progress and safely rerun after resolving the cause.')
