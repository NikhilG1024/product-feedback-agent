"""Pure normalization and chronological dataset contracts; no reviewer identifiers."""
import hashlib
import json
from datetime import datetime, timezone

DATASET = 'amazon-reviews-2023-electronics-headphones-v1'
SOURCE = 'https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023'

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def alias(row, primary, alternate):
    if primary in row and alternate in row and row[primary] != row[alternate]:
        raise ValueError(f'Conflicting source aliases: {primary}/{alternate}')
    return row.get(primary, row.get(alternate))

def timestamp_ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError('Timestamp must be integral seconds or milliseconds')
    value = int(value)
    if 800_000_000 <= value < 1_700_000_000:
        value *= 1000
    if not 800_000_000_000 <= value < 1_696_118_400_000:
        raise ValueError('Timestamp outside dataset historical range through September 2023')
    return value

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def normalize(row, line, revision, *, min_words=20):
    stamp = timestamp_ms(alias(row, 'timestamp', 'sort_timestamp'))
    rating = row['rating']
    if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 1 <= rating <= 5:
        raise ValueError('Invalid rating')
    for field in ('parent_asin', 'asin', 'title', 'text'):
        if not isinstance(row.get(field), str):
            raise ValueError(f'Missing/invalid {field}')
    if not row['parent_asin'] or not row['asin'] or len(row['text'].split()) < min_words:
        raise ValueError(f'Review requires at least {min_words} words')
    votes = alias(row, 'helpful_vote', 'helpful_votes')
    if votes is not None and (type(votes) is not int or votes < 0):
        raise ValueError('Invalid helpful votes')
    verified = row.get('verified_purchase')
    if verified is not None and type(verified) is not bool:
        raise ValueError('Invalid verified purchase')
    core = {k:row[k] for k in ('parent_asin', 'asin', 'title', 'text')}
    core.update(timestamp_ms=stamp, rating=float(rating))
    identity = digest(core)
    return dict(_id='ar23:'+identity, **core, timestamp=iso(stamp), helpful_vote=votes,
                verified_purchase=verified, dataset_id=DATASET,
                provenance=dict(dataset='Amazon Reviews 2023', revision=revision,
                                file='raw/review_categories/Electronics.jsonl', line=line,
                                source=SOURCE, content_sha256=identity))

def deduplicate(rows):
    seen = {}; duplicates = 0
    for row in rows:
        previous = seen.get(row['_id'])
        if previous:
            # Same content at multiple source lines is a duplicate; other differences are conflicts.
            clean = lambda x: {k:v for k,v in x.items() if k != 'provenance'}
            if clean(row) != clean(previous):
                raise ValueError('Identity collision or conflicting duplicate: '+row['_id'])
            duplicates += 1
        else:
            seen[row['_id']] = row
    return list(seen.values()), duplicates

def assign_batches(rows):
    times = sorted({r['timestamp_ms'] for r in rows})
    if len(times) < 3:
        raise ValueError('At least three distinct timestamps required')
    first = times[max(0, int(len(times)*.5)-1)]
    second = times[max(1, int(len(times)*.75)-1)]
    for row in rows:
        label = 'A' if row['timestamp_ms'] <= first else 'B' if row['timestamp_ms'] <= second else 'C'
        row.update(batch=label, batch_id=DATASET+':'+label, held_out=label=='C')
    batches=[]
    for label in 'ABC':
        group=[r for r in rows if r['batch']==label]
        batches.append(dict(_id=DATASET+':'+label,dataset_id=DATASET,label=label,held_out=label=='C',
                            start_at=min(r['timestamp'] for r in group),end_at=max(r['timestamp'] for r in group),
                            review_count=len(group),product_counts={p:sum(r['parent_asin']==p for r in group) for p in sorted({r['parent_asin'] for r in rows})}))
    return batches

def review_filter(parent_asin, batch, *, evaluation=False):
    if batch not in 'ABC' or len(batch)!=1 or (batch=='C' and not evaluation):
        raise ValueError('Batch C requires explicit evaluation access')
    return dict(parent_asin=parent_asin,batch_id=DATASET+':'+batch,held_out=batch=='C')

def validate_dataset(products, reviews, batches, *, max_products=5, max_reviews=1500):
    for rows in (products,reviews,batches):
        if len({r['_id'] for r in rows})!=len(rows):raise ValueError('Duplicate identity')
    product_ids={p['_id'] for p in products};batch_map={b['_id']:b for b in batches}
    if len(products)<3 or len(products)>max_products or not 500<=len(reviews)<=max_reviews:raise ValueError('Unexpected dataset size')
    if {b['label'] for b in batches}!=set('ABC') or len(batches)!=3:raise ValueError('Invalid batches')
    for review in reviews:
        if review['parent_asin'] not in product_ids or review['batch_id'] not in batch_map:raise ValueError('Broken reference')
        batch=batch_map[review['batch_id']]
        if review['batch']!=batch['label'] or review['held_out']!=(review['batch']=='C') or review['dataset_id']!=DATASET:raise ValueError('Batch isolation mismatch')
        if review['timestamp']!=iso(review['timestamp_ms']):raise ValueError('Timestamp representations disagree')
        core={k:review[k] for k in ('parent_asin','asin','title','text','timestamp_ms','rating')}
        if review['_id']!='ar23:'+digest(core):raise ValueError('Review content/identity mismatch')
    for batch in batches:
        rows=[r for r in reviews if r['batch_id']==batch['_id']]
        if not rows or batch['held_out']!=(batch['label']=='C') or batch['dataset_id']!=DATASET:raise ValueError('Invalid batch')
        if batch['review_count']!=len(rows) or batch['product_counts']!={p:sum(r['parent_asin']==p for r in rows) for p in sorted(product_ids)}:raise ValueError('Batch counts disagree')
        if min(r['timestamp'] for r in rows)!=batch['start_at'] or max(r['timestamp'] for r in rows)!=batch['end_at']:raise ValueError('Batch dates disagree')
        if not all(batch['product_counts'].values()):raise ValueError('Product missing from batch')
    by_label={b['label']:b for b in batches}
    if not by_label['A']['end_at']<by_label['B']['start_at']<=by_label['B']['end_at']<by_label['C']['start_at']:raise ValueError('Chronological overlap')
    return {'unique_review_ids':True,'foreign_keys':True,'strict_global_chronology':True,'all_products_in_all_batches':True}
