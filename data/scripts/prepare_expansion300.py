"""Prepare the expanded bundle without changing source identity or existing batches."""
import json,hashlib
from collections import Counter
from pathlib import Path
from pipeline import DATASET,SOURCE,canonical,validate_dataset
from extract import ROOT,REVISION
from expansion_types import TYPES
RAW=ROOT/'data/raw/expansion300'
OUT=ROOT/'data/processed/expansion300'
SOURCE_MANIFEST={'dataset':'Amazon Reviews 2023','source':SOURCE,'revision':REVISION,'review_file':'raw/review_categories/Electronics.jsonl','metadata_file':'raw/meta_categories/meta_Electronics.jsonl'}

def load(path):return [json.loads(line) for line in path.read_text().splitlines()]

def prepare():
 selection=json.loads((RAW/'selection.json').read_text())
 if not selection['target_met'] and not selection.get('staged'):raise ValueError('Source selection is incomplete and is not an explicit frozen stage')
 metadata=json.loads((RAW/'selected-metadata.json').read_text());reviews=load(RAW/'reviews.jsonl')
 incoming_by_id={r['_id']:r for r in reviews}
 old_products={r['_id']:r for r in load(ROOT/'data/processed/products.jsonl')}
 old_reviews={r['_id']:r for r in load(ROOT/'data/processed/reviews.jsonl')}
 if any(incoming_by_id.get(key)!=row for key,row in old_reviews.items()):raise ValueError('An existing review changed or is absent')
 products=[]
 for m in metadata:
  key=m['parent_asin']
  if key in old_products:row=dict(old_products[key])
  else:row=dict(_id=key,title=m['title'],store=m.get('store'),categories=m.get('categories',[]),provenance=dict(dataset='Amazon Reviews 2023',source=SOURCE,revision=REVISION,file=SOURCE_MANIFEST['metadata_file'],line=m['source_line'],metadata_sha256=m['metadata_sha256'],note='Static crawl metadata; not point-in-time product history'))
  row['product_type']=m['product_type'];products.append(row)
 by_product=Counter(r['parent_asin'] for r in reviews);type_counts=Counter(p['product_type'] for p in products)
 if not set(type_counts).issubset(TYPES) or set(type_counts.values())!={20} or len(by_product)!=len(type_counts)*20 or set(by_product.values())!={1000}:raise ValueError('Target cardinalities differ')
 batches=[]
 for label in 'ABC':
  group=[r for r in reviews if r['batch']==label]
  batches.append(dict(_id=DATASET+':'+label,dataset_id=DATASET,label=label,held_out=label=='C',start_at=min(r['timestamp'] for r in group),end_at=max(r['timestamp'] for r in group),review_count=len(group),product_counts={p:sum(r['parent_asin']==p for r in group) for p in sorted(by_product)},source_manifest=SOURCE_MANIFEST,ingestion_status='complete'))
 checks=validate_dataset(products,reviews,batches,max_products=300,max_reviews=300000)
 OUT.mkdir(exist_ok=True)
 for name,rows in [('products',products),('reviews',reviews),('batches',batches)]:
  with (OUT/(name+'.jsonl')).open('w') as f:
   for row in sorted(rows,key=lambda r:r['_id']):f.write(canonical(row)+'\n')
 from bson import BSON
 from datetime import datetime
 bson_bytes=0
 for name,rows in [('products',products),('reviews',reviews),('batches',batches)]:
  for row in rows:
   doc=dict(row)
   for field in {'reviews':['timestamp'],'batches':['start_at','end_at']}.get(name,[]):doc[field]=datetime.fromisoformat(doc[field].replace('Z','+00:00'))
   bson_bytes+=len(BSON.encode(doc))
 report=dict(status='prepared_not_imported',dataset_id=DATASET,products=len(products),reviews=len(reviews),types=len(type_counts),target_products=300,target_reviews=300000,target_types=15,target_met=selection['target_met'],products_per_type=20,reviews_per_product=1000,source_selection=selection,
  counts_by_type=dict(type_counts),checks=checks,bson_bytes=bson_bytes,original_reviews_preserved=len(old_reviews),
  review_length_policy='New records: >=5 words, <=1000 text characters, <=300 title characters; full text preserved. Initial 547 records exempt and unchanged.',
  files_sha256={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in ['products.jsonl','reviews.jsonl','batches.jsonl']})
 (OUT/'preparation-report.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ['status','products','reviews','types','bson_bytes']}))

if __name__=='__main__':prepare()
