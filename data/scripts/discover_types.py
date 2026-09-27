"""Inspect product-type availability without persisting media or reviewer information."""
import json,urllib.request
from collections import Counter
from pipeline import SOURCE,digest
from extract import ROOT,REVISION,PRODUCTS
OUT=ROOT/'data/raw/expansion300'

def main():
 OUT.mkdir(exist_ok=True);found=[];counts=Counter()
 url=SOURCE+'/resolve/'+REVISION+'/raw/meta_categories/meta_Electronics.jsonl'
 with urllib.request.urlopen(url,timeout=90) as response:
  for number,line in enumerate(response,1):
   row=json.loads(line)
   if row.get('rating_number',0)>=5000 or row.get('parent_asin') in PRODUCTS:
    cats=row.get('categories',[])
    if cats or row['parent_asin'] in PRODUCTS:
     counts[cats[-1] if cats else 'Known original headphone']+=1
     found.append(dict(parent_asin=row['parent_asin'],title=row['title'],store=row.get('store'),categories=cats,
                       rating_number=row.get('rating_number',0),source_line=number,
                       metadata_sha256=digest({k:v for k,v in row.items() if k not in ('images','videos')})))
   if number%50000==0:print(json.dumps({'metadata_scanned':number,'candidates':len(found),'leading_types':counts.most_common(25)}),flush=True)
   # Scan the pinned metadata file to EOF for sufficient category coverage.
 (OUT/'metadata-candidates.json').write_text(json.dumps(found,ensure_ascii=False))
 (OUT/'type-counts.json').write_text(json.dumps(counts.most_common(),indent=2))
 print(json.dumps({'status':'complete','candidates':len(found),'leading_types':counts.most_common(50)}),flush=True)

if __name__=='__main__':main()
