"""Stream candidates from pinned source; retain at most 1,000 reviews/product in memory."""
import json,re,time,urllib.request
from collections import Counter
from pipeline import SOURCE,normalize,DATASET,canonical
from extract import ROOT,REVISION,PRODUCTS,MIN_TIMESTAMP
OUT=ROOT/'data/raw/expansion50'
PATTERN=re.compile(rb'"parent_asin"\s*:\s*"([^"\\]+)"')
A_END=1651170814534
B_END=1666833193233

def label(ms):return 'A' if ms<=A_END else 'B' if ms<=B_END else 'C'

def main():
 metadata=json.loads((OUT/'metadata.json').read_text());candidates={m['parent_asin']:m for m in metadata}
 groups={p:{} for p in candidates};batch_counts={p:Counter() for p in candidates};duplicates=0;invalid=0
 for line in (ROOT/'data/processed/reviews.jsonl').open():
  row=json.loads(line);groups[row['parent_asin']][row['_id']]=row;batch_counts[row['parent_asin']][row['batch']]+=1
 original_ids={r for g in groups.values() for r in g}
 url=SOURCE+'/resolve/'+REVISION+'/raw/review_categories/Electronics.jsonl'
 count=0;offset=0;started=time.monotonic();errors=0;done=False
 def eligible():return [p for p,g in groups.items() if len(g)==1000 and all(batch_counts[p][b]>0 for b in 'ABC')]
 def progress(status):
  ready=eligible();record=dict(status=status,source_rows_scanned=count,source_bytes_streamed=offset,retained_candidate_reviews=sum(map(len,groups.values())),
   eligible_products=len(ready),target_products=50,reviews_per_product=1000,original_product_counts={p:len(groups[p]) for p in PRODUCTS},
   duplicates_removed=duplicates,invalid_rows=invalid,elapsed_seconds=round(time.monotonic()-started))
  temporary=OUT/'progress.tmp';temporary.write_text(json.dumps(record,indent=2));temporary.replace(OUT/'progress.json');print(json.dumps(record),flush=True)
 def request():
  headers={'Range':f'bytes={offset}-'} if offset else {}
  response=urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=90)
  if offset and (response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')):
   response.close();raise ValueError('Server did not honor exact byte resume')
  return response
 while not done:
  try:
   with request() as response:
    while True:
     line=response.readline()
     if not line:done=True;break
     offset+=len(line);count+=1
     match=PATTERN.search(line);parent=match.group(1).decode() if match else ''
     if parent in groups:
      group=groups[parent]
      # Once full and all batches represented, no more records are retained for this candidate.
      if len(group)<1000 or not all(batch_counts[parent][b] for b in 'ABC'):
       row=json.loads(line)
       if row.get('timestamp',0)>=MIN_TIMESTAMP and len(row.get('text','').split())>=20:
        try:review=normalize(row,count,REVISION)
        except ValueError:invalid+=1;review=None
        if review:
         batch=label(review['timestamp_ms']);review.update(batch=batch,batch_id=DATASET+':'+batch,held_out=batch=='C')
         if review['_id'] in group:duplicates+=1
         elif len(group)<1000:
          group[review['_id']]=review;batch_counts[parent][batch]+=1
         elif batch_counts[parent][batch]==0:
          # Ensure temporal representation without removing any original imported review.
          victim=next((x for x in reversed(list(group.values())) if x['_id'] not in original_ids and batch_counts[parent][x['batch']]>1),None)
          if victim:
           del group[victim['_id']];batch_counts[parent][victim['batch']]-=1;group[review['_id']]=review;batch_counts[parent][batch]+=1
     if count%250000==0:progress('scanning')
     ready=eligible() if count%250000==0 else []
     if len(ready)>=50 and set(PRODUCTS).issubset(ready):done=True;break
  except (TimeoutError,OSError) as exc:
   errors+=1;progress('retrying_stream')
   if errors>3:raise RuntimeError('Source stream failed repeatedly; no import performed') from exc
 # Original products are mandatory. Remaining eligible candidates rank by metadata rating_number.
 ready=eligible()
 if not set(PRODUCTS).issubset(ready):raise ValueError('Source exhausted before every original product reached 1,000 reviews')
 chosen=sorted(ready,key=lambda p:(p not in PRODUCTS,-candidates[p].get('rating_number',0),p))[:50]
 with (OUT/'reviews.jsonl').open('w') as dest:
  for p in chosen:
   for row in sorted(groups[p].values(),key=lambda r:(r['timestamp_ms'],r['_id'])):dest.write(canonical(row)+'\n')
 (OUT/'selected-metadata.json').write_text(json.dumps([candidates[p] for p in chosen],ensure_ascii=False))
 (OUT/'selection.json').write_text(json.dumps(dict(revision=REVISION,products=chosen,counts={p:len(groups[p]) for p in chosen},fixed_a_end_ms=A_END,fixed_b_end_ms=B_END,source_rows_scanned=count,source_bytes_streamed=offset),indent=2))
 progress('complete')

if __name__=='__main__':main()
