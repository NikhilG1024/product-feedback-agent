"""Bounded per-product source sampling for 15 types × 20 products × 1,000 reviews.
Full text is preserved for selected rows; 5-word minimum and 1,000-character maximum
bound storage. Original imported records are always retained, including longer texts.
"""
import json,re,time,urllib.request
from collections import Counter
from pathlib import Path
from pipeline import SOURCE,normalize,DATASET,canonical,iso,digest
from extract import ROOT,REVISION,PRODUCTS
from expansion_types import TYPES,product_type
OUT=ROOT/'data/raw/expansion300'
PATTERN=re.compile(rb'"parent_asin"\s*:\s*"([^"\\]+)"')
A_END=1651170814534
B_END=1666833193233

def label(ms):return 'A' if ms<=A_END else 'B' if ms<=B_END else 'C'

def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');args=parser.parse_args()
 metadata=json.loads((OUT/'metadata-candidates.json').read_text());by_type={t:[] for t in TYPES}
 known={r['parent_asin'] for r in metadata}
 for row in json.loads((ROOT/'data/raw/candidate_metadata.json').read_text()):
  if row['parent_asin'] in PRODUCTS and row['parent_asin'] not in known:
   metadata.append(dict(parent_asin=row['parent_asin'],title=row['title'],store=row.get('store'),categories=row.get('categories',[]),rating_number=row.get('rating_number',0),source_line=row['_source_line'],metadata_sha256=digest({k:v for k,v in row.items() if k!='_source_line'})))
 for row in metadata:
  kind=product_type(row)
  if kind:row['product_type']=kind;by_type[kind].append(row)
 candidates={}
 for kind,rows in by_type.items():
  selected=sorted(rows,key=lambda r:(r['parent_asin'] not in PRODUCTS,-r['rating_number'],r['parent_asin']))
  if len(selected)<20:raise ValueError('Fewer than 20 metadata candidates for '+kind)
  candidates.update({r['parent_asin']:r for r in selected})
 if not set(PRODUCTS).issubset(candidates):raise ValueError('Original products absent from candidates')
 groups={p:{} for p in candidates};counts={p:Counter() for p in candidates}
 for line in (ROOT/'data/processed/reviews.jsonl').open():
  r=json.loads(line);groups[r['parent_asin']][r['_id']]=r;counts[r['parent_asin']][r['batch']]+=1
 original_ids={rid for group in groups.values() for rid in group}
 url=SOURCE+'/resolve/'+REVISION+'/raw/review_categories/Electronics.jsonl'
 offset=0;number=0;duplicates=0;invalid=0;started=time.monotonic();done=False;retries=0
 if args.resume and (OUT/'candidate-checkpoint.jsonl').exists():
  for line in (OUT/'candidate-checkpoint.jsonl').open():
   record=json.loads(line)
   if '_checkpoint' in record:
    state=record['_checkpoint'];offset=state['offset'];number=state['number'];duplicates=state['duplicates'];invalid=state['invalid']
   else:
    p=record['parent_asin']
    if p in groups:groups[p][record['_id']]=record
  counts={p:Counter(r['batch'] for r in rows.values()) for p,rows in groups.items()}
 def checkpoint():
  temporary=OUT/'candidate-checkpoint.tmp'
  with temporary.open('w') as f:
   for group in groups.values():
    for row in group.values():f.write(canonical(row)+'\n')
   f.write(canonical({'_checkpoint':{'offset':offset,'number':number,'duplicates':duplicates,'invalid':invalid}})+'\n')
  temporary.replace(OUT/'candidate-checkpoint.jsonl')
 def eligible():return {t:[p for p in candidates if candidates[p]['product_type']==t and len(groups[p])==1000 and all(counts[p][b]>=1 for b in 'ABC')] for t in TYPES}
 def emit(status):
  ready=eligible();report=dict(status=status,source_rows_scanned=number,source_bytes_streamed=offset,
    candidate_products=len(candidates),retained_candidate_reviews=sum(map(len,groups.values())),eligible_products_by_type={t:len(p) for t,p in ready.items()},
    target_types=15,products_per_type=20,reviews_per_product=1000,original_product_counts={p:len(groups[p]) for p in PRODUCTS},
    duplicates_skipped=duplicates,invalid_rows=invalid,elapsed_seconds=round(time.monotonic()-started))
  temp=OUT/'progress.tmp';temp.write_text(json.dumps(report,indent=2));temp.replace(OUT/'progress.json');print(json.dumps(report),flush=True)
 def complete():
  ready=eligible();return all(len(x)>=20 for x in ready.values()) and all(p in ready['Headphones'] for p in PRODUCTS)
 while not done:
  try:
   headers={'Range':f'bytes={offset}-'} if offset else {}
   with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=90) as response:
    if offset and (response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')):raise ValueError('Exact resume not honored')
    while True:
     line=response.readline()
     if not line:done=True;break
     offset+=len(line);number+=1
     match=PATTERN.search(line);parent=match.group(1).decode() if match else ''
     if parent in groups:
      group=groups[parent]
      if len(group)<1000 or not all(counts[parent][b]>=1 for b in 'ABC'):
       source=json.loads(line);body=source.get('text','')
       if 5<=len(body.split()) and len(body)<=1000 and len(source.get('title',''))<=300:
        try:row=normalize(source,number,REVISION,min_words=5)
        except ValueError:invalid+=1;row=None
        if row:
         b=label(row['timestamp_ms']);row.update(batch=b,batch_id=DATASET+':'+b,held_out=b=='C')
         # The batch's source manifest resolves revision/path; retain exact upstream row location.
         row['provenance']={'revision':REVISION,'line':number}
         if row['_id'] in group:duplicates+=1
         elif len(group)<1000:group[row['_id']]=row;counts[parent][b]+=1
         elif counts[parent][b]<1:
          victim=next((v for v in reversed(list(group.values())) if v['_id'] not in original_ids and counts[parent][v['batch']]>1),None)
          if victim:
           del group[victim['_id']];counts[parent][victim['batch']]-=1;group[row['_id']]=row;counts[parent][b]+=1
     if number%5000000==0:checkpoint()
     if number%250000==0:
      emit('scanning')
      if complete():done=True;break
  except KeyboardInterrupt:
   checkpoint();emit('paused_interrupted');raise
  except (TimeoutError,OSError) as exc:
   retries+=1;emit('retrying_source')
   if retries>3:raise RuntimeError('Source read failed repeatedly; no import performed') from exc
 checkpoint()
 ready=eligible();chosen=[]
 locked=json.loads((OUT/'locked-products.json').read_text()) if (OUT/'locked-products.json').exists() else {}
 for kind,ids in ready.items():
  prior=locked.get(kind,[])
  if any(p not in ids for p in prior):raise ValueError('A previously selected product no longer qualifies')
  chosen.extend(prior+[p for p in sorted(ids,key=lambda p:(p not in PRODUCTS,-candidates[p]['rating_number'],p)) if p not in prior][:20-len(prior)])
 # Preserve a complete checkpoint even if the source cannot supply the full requested counts.
 with (OUT/'reviews.jsonl').open('w') as dest:
  for parent in chosen:
   for row in sorted(groups[parent].values(),key=lambda r:(r['timestamp_ms'],r['_id'])):dest.write(canonical(row)+'\n')
 (OUT/'selected-metadata.json').write_text(json.dumps([candidates[p] for p in chosen],ensure_ascii=False))
 (OUT/'selection.json').write_text(json.dumps({'revision':REVISION,'source_rows_scanned':number,'source_bytes_streamed':offset,'products_by_type':{t:[p for p in chosen if candidates[p]['product_type']==t] for t in TYPES},'target_met':complete(),'fixed_a_end_ms':A_END,'fixed_b_end_ms':B_END},indent=2))
 emit('complete' if complete() else 'source_exhausted_target_shortfall')

if __name__=='__main__':main()
