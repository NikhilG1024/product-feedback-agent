"""Discover metadata-confirmed headphone candidates in a bounded pinned source prefix."""
import json,urllib.request
from pathlib import Path
from pipeline import SOURCE
from extract import ROOT,REVISION,PRODUCTS
OUT=ROOT/'data/raw/expansion50'

def main():
 OUT.mkdir(exist_ok=True)
 found={}
 url=SOURCE+'/resolve/'+REVISION+'/raw/meta_categories/meta_Electronics.jsonl'
 with urllib.request.urlopen(url,timeout=90) as response:
  for number,line in enumerate(response,1):
   if b'Headphones & Earbuds' in line or any(p.encode() in line for p in PRODUCTS):
    row=json.loads(line)
    if (('Headphones & Earbuds' in row.get('categories',[]) and row.get('rating_number',0)>=5000) or row.get('parent_asin') in PRODUCTS):
     row.pop('images',None);row.pop('videos',None);row['_source_line']=number;found[row['parent_asin']]=row
   if number%50000==0:print(json.dumps({'metadata_scanned':number,'candidates':len(found)}),flush=True)
   if number>=300000:break
 selected=sorted(found.values(),key=lambda x:(x['parent_asin'] not in PRODUCTS,-x.get('rating_number',0),x['parent_asin']))[:250]
 if not set(PRODUCTS).issubset({x['parent_asin'] for x in selected}):raise ValueError('Original products absent')
 (OUT/'metadata.json').write_text(json.dumps(selected,ensure_ascii=False))
 (OUT/'metadata-scan.json').write_text(json.dumps({'revision':REVISION,'metadata_rows_scanned':number,'eligible_candidates':len(found),'retained_candidates':len(selected)},indent=2))
 print(json.dumps({'status':'complete','retained_candidates':len(selected)}),flush=True)

if __name__=='__main__':main()
