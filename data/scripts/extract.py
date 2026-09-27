"""Stream a pinned bounded source slice, then build the historical demo subset."""
import argparse
import collections
import json
import urllib.request
from pathlib import Path
from pipeline import DATASET, SOURCE, canonical, digest, normalize, deduplicate, assign_batches, validate_dataset

ROOT=Path(__file__).resolve().parents[2]
REVISION='2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e'
PRODUCTS=['B07S764D9V','B0BHWYQ47Y','B0C338S8M7']
MIN_TIMESTAMP=1625097600000 # 2021-07-01 UTC, common coverage after Beats launch
MAX_LINES=4_000_000

def write_jsonl(path,rows):
    path.write_text(''.join(canonical(x)+'\n' for x in rows))

def download():
    raw=ROOT/'data/raw';raw.mkdir(parents=True,exist_ok=True)
    base=SOURCE+'/resolve/'+REVISION+'/raw/'
    meta=[]
    with urllib.request.urlopen(base+'meta_categories/meta_Electronics.jsonl',timeout=60) as response:
        for line_number,line in enumerate(response,1):
            row=json.loads(line)
            if row.get('parent_asin') in PRODUCTS:
                row.pop('images',None);row.pop('videos',None);row['_source_line']=line_number;meta.append(row)
            if line_number>=30000:break
    if {m['parent_asin'] for m in meta}!=set(PRODUCTS):raise ValueError('Selected metadata not found')
    (raw/'candidate_metadata.json').write_text(json.dumps(meta))
    temporary=raw/'selected-download.tmp'
    with temporary.open('w') as output,urllib.request.urlopen(base+'review_categories/Electronics.jsonl',timeout=60) as response:
        for line_number,line in enumerate(response,1):
            row=json.loads(line)
            if row.get('parent_asin') in PRODUCTS and len(row.get('text','').split())>=20:
                row.pop('images',None);row.pop('user_id',None);row['_source_line']=line_number
                output.write(canonical(row)+'\n')
            if line_number%250000==0:print('Scanned source rows:',line_number,flush=True)
            if line_number>=MAX_LINES:break
    if line_number!=MAX_LINES:raise ValueError('Source ended early')
    temporary.replace(raw/'candidate_reviews.jsonl')
    (raw/'revision.txt').write_text(REVISION)
    (raw/'discovery.json').write_text(json.dumps(dict(revision=REVISION,metadata_scanned=30000,reviews_scanned=MAX_LINES)))

def prepare():
    raw=ROOT/'data/raw';out=ROOT/'data/processed';out.mkdir(parents=True,exist_ok=True)
    discovery=json.loads((raw/'discovery.json').read_text())
    if discovery['revision']!=REVISION or discovery['reviews_scanned']!=MAX_LINES:raise ValueError('Unexpected cached slice')
    meta={m['parent_asin']:m for m in json.loads((raw/'candidate_metadata.json').read_text())}
    rows=[];aliases=collections.Counter();invalid=[]
    for line in (raw/'candidate_reviews.jsonl').open():
        row=json.loads(line)
        if row['parent_asin'] not in PRODUCTS:continue
        try:review=normalize(row,row['_source_line'],REVISION)
        except ValueError as exc:
            invalid.append({'source_line':row['_source_line'],'reason':str(exc)});continue
        if review['timestamp_ms']<MIN_TIMESTAMP:continue
        aliases.update(k for k in ('timestamp','sort_timestamp','helpful_vote','helpful_votes') if k in row)
        rows.append(review)
    rows,duplicates=deduplicate(rows)
    rows.sort(key=lambda r:(r['timestamp_ms'],r['_id']))
    if not 500<=len(rows)<=1500:raise ValueError('Subset outside requested target')
    batches=assign_batches(rows)
    products=[]
    for key in PRODUCTS:
        m=meta[key]
        products.append(dict(_id=key,title=m['title'],store=m.get('store'),categories=m.get('categories',[]),
                             provenance=dict(dataset='Amazon Reviews 2023',source=SOURCE,revision=REVISION,
                                             file='raw/meta_categories/meta_Electronics.jsonl',line=m['_source_line'],
                                             metadata_sha256=digest({k:v for k,v in m.items() if k!='_source_line'}),
                                             note='Static crawl metadata; not point-in-time product history')))
    for name,records in [('products',products),('reviews',rows),('batches',batches)]:write_jsonl(out/(name+'.jsonl'),records)
    counts={p:{b:sum(r['parent_asin']==p and r['batch']==b for r in rows) for b in 'ABC'} for p in PRODUCTS}
    if not all(n>0 for groups in counts.values() for n in groups.values()):raise ValueError('Product absent from a batch')
    # Only A informs concern vocabulary inspection; these are literal matches, not labels/findings.
    vocabulary={p:{word:sum(r['parent_asin']==p and r['batch']=='A' and r['rating']<=3 and word in r['text'].lower()
                          for r in rows) for word in ('battery','sound','fit','stopped','comfort')} for p in PRODUCTS}
    import hashlib
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.jsonl') if p.stem in ('products','reviews','batches')}
    report=dict(status='local_preparation_verified_mongodb_not_loaded',dataset_id=DATASET,revision=REVISION,
                source_review_lines_scanned=MAX_LINES,source_metadata_lines_scanned=30000,
                products=len(products),reviews=len(rows),batches=batches,counts_by_product_batch=counts,
                duplicates_removed=duplicates,invalid_rows=invalid,actual_source_aliases=dict(aliases),
                timestamp_unit='milliseconds; seconds aliases accepted only after historical-range validation',
                batch_a_low_rating_literal_mentions=vocabulary,files_sha256=hashes,
                processed_jsonl_bytes=sum((out/name).stat().st_size for name in hashes),
                validation=validate_dataset(products,rows,batches),
                limitations=['Bounded first 4M source rows, not random or complete product histories.',
                             'At least 20 whitespace-separated words; selection favors substantive long reviews.',
                             'Metadata is crawl-time product identity context; no point-in-time features or aggregate ratings imported.',
                             'Content deduplication can merge indistinguishable reviews from different reviewers; reviewer identity intentionally omitted.',
                             'Batch flags/query helper are application guards, not database access control.'])
    (out/'local-validation.json').write_text(json.dumps(report,indent=2))
    sample=[]
    for p in PRODUCTS:sample.extend([r for r in rows if r['parent_asin']==p and r['batch']=='A'][:2])
    write_jsonl(ROOT/'data/samples/reviews_A.jsonl',sample)
    write_jsonl(ROOT/'data/samples/products.jsonl',products)
    print(json.dumps({k:report[k] for k in ('products','reviews','counts_by_product_batch','duplicates_removed','processed_jsonl_bytes')},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--download',action='store_true');args=parser.parse_args()
    if args.download:download()
    prepare()
