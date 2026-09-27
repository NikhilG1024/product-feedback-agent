"""Freeze complete 20-product groups from a checkpoint; later scans only add types."""
import json
from collections import Counter,defaultdict
from pipeline import canonical,digest
from extract import ROOT,PRODUCTS,REVISION
from expansion_types import TYPES,product_type
OUT=ROOT/'data/raw/expansion300'

def main():
 metadata={r['parent_asin']:r for r in json.loads((OUT/'metadata-candidates.json').read_text())}
 for row in json.loads((ROOT/'data/raw/candidate_metadata.json').read_text()):
  if row['parent_asin'] in PRODUCTS and row['parent_asin'] not in metadata:
   metadata[row['parent_asin']]=dict(parent_asin=row['parent_asin'],title=row['title'],store=row.get('store'),categories=row.get('categories',[]),rating_number=row.get('rating_number',0),source_line=row['_source_line'],metadata_sha256=digest({k:v for k,v in row.items() if k!='_source_line'}))
 for row in metadata.values():row['product_type']=product_type(row)
 count=Counter();batch=defaultdict(Counter);state=None
 locked=json.loads((OUT/'locked-products.json').read_text()) if (OUT/'locked-products.json').exists() else {}
 # Keep one inode open for both passes even if the active scanner writes a newer checkpoint.
 with (OUT/'candidate-checkpoint.jsonl').open() as source:
  for line in source:
   row=json.loads(line)
   if '_checkpoint' in row:state=row['_checkpoint'];continue
   count[row['parent_asin']]+=1;batch[row['parent_asin']][row['batch']]+=1
  for kind in TYPES:
   if kind in locked:continue
   eligible=[p for p,n in count.items() if n==1000 and metadata[p]['product_type']==kind and all(batch[p][b]>=1 for b in 'ABC')]
   if len(eligible)>=20 and (kind!='Headphones' or set(PRODUCTS).issubset(eligible)):
    locked[kind]=sorted(eligible,key=lambda p:(p not in PRODUCTS,-metadata[p]['rating_number'],p))[:20]
  if 'Headphones' not in locked:raise ValueError('Original products are not all ready; no stage prepared')
  chosen={p for group in locked.values() for p in group}
  source.seek(0)
  temporary=OUT/'reviews-stage.tmp'
  with temporary.open('w') as dest:
   for line in source:
    row=json.loads(line)
    if row.get('parent_asin') in chosen:dest.write(canonical(row)+'\n')
  temporary.replace(OUT/'reviews.jsonl')
 (OUT/'locked-products.json').write_text(json.dumps(locked,indent=2))
 (OUT/'selected-metadata.json').write_text(json.dumps([metadata[p] for p in sorted(chosen)],ensure_ascii=False))
 selection=dict(revision=REVISION,source_rows_scanned=state['number'],source_bytes_streamed=state['offset'],products_by_type=locked,target_met=len(locked)==15,staged=True,fixed_a_end_ms=1651170814534,fixed_b_end_ms=1666833193233)
 (OUT/'selection.json').write_text(json.dumps(selection,indent=2));print(json.dumps({'ready_types':list(locked),'products':len(chosen),'reviews':len(chosen)*1000,'target_met':selection['target_met']}))

if __name__=='__main__':main()
