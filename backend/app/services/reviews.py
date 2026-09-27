"""Reviewer use cases with immutable payload identities and stable pagination."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from app.domain import Principal, ReviewInput
from app.errors import ServiceError
from app.repositories.reviews import ReviewRepository


def cursor_encode(stamp, record_id):
    return base64.urlsafe_b64encode(json.dumps([stamp.isoformat(),record_id]).encode()).decode()


def cursor_decode(cursor):
    try:
        if len(cursor)>2048: raise ValueError()
        stamp, record_id=json.loads(base64.b64decode(cursor,altchars=b'-_',validate=True))
        stamp=datetime.fromisoformat(stamp)
        if stamp.tzinfo is None or not isinstance(record_id,str) or not record_id: raise ValueError()
        return stamp,record_id
    except (ValueError,TypeError,UnicodeError):
        raise ServiceError('invalid_cursor',422) from None


def feed_cursor_encode(row, sort, source, batch_id, sentiment, rating):
    key=([row['_feed_source'],row['_feed_sentiment']] if sort=='priority' else [])+[row['timestamp'].isoformat(),row['_id']]
    data={'v':2,'sort':sort,'source':source,'batch_id':batch_id,'sentiment':sentiment,'rating':rating,'key':key}
    return base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode()


def feed_cursor_decode(cursor, sort, source, batch_id, sentiment, rating):
    try:
        if len(cursor)>2048: raise ValueError()
        data=json.loads(base64.b64decode(cursor,altchars=b'-_',validate=True))
        if (not isinstance(data,dict) or data.get('v')!=2 or
                any(data.get(k)!=v for k,v in {'sort':sort,'source':source,'batch_id':batch_id,
                    'sentiment':sentiment,'rating':rating}.items())): raise ValueError()
        key=data['key']
        if not isinstance(key,list) or len(key)!=(4 if sort=='priority' else 2): raise ValueError()
        if sort=='priority':
            if type(key[0]) is not int or key[0] not in (0,1) or type(key[1]) is not int or key[1] not in (0,1,2): raise ValueError()
        stamp=datetime.fromisoformat(key[-2])
        if stamp.tzinfo is None or not isinstance(key[-1],str) or not key[-1]: raise ValueError()
        return (*key[:2],stamp,key[-1]) if sort=='priority' else (stamp,key[-1])
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise ServiceError('invalid_cursor',422) from None


def public_review(row):
    return {'id':row['_id'],'parent_asin':row['parent_asin'],'asin':row['asin'],'title':row['title'],'text':row['text'],
            'rating':row['rating'],'timestamp':row['timestamp'],'source':row.get('source','amazon_2023'),
            'batch_id':row.get('batch_id'),'processing':row.get('processing'),
            'provisional_findings':row.get('provisional_findings') if row.get('processing',{}).get('status')=='completed' else None,
            'guidance_references':row.get('guidance_references') if row.get('processing',{}).get('status')=='completed' else None}


class ReviewService:
    def __init__(self, database, submission_limit=10, *, capacity=None, summaries=None):
        self.repository=ReviewRepository(database, capacity=capacity)
        self.submission_limit=submission_limit
        self.summaries=summaries

    def review_batches(self, product_id: str, principal: Principal) -> dict:
        if principal.role != 'pm': raise ServiceError('forbidden',403)
        if self.repository.product(product_id) is None: raise ServiceError('product_not_found',404)
        rows=self.repository.available_batches(product_id)
        if len(rows)>100: raise ServiceError('too_many_review_batches',503)
        return {'items':[{'id':row['_id'],'label':row['label'],
                          'review_count':row['product_counts'][product_id]} for row in rows]}

    def submit(self, product_id: str, principal: Principal, key: str, payload: ReviewInput) -> dict:
        if principal.role!='reviewer': raise ServiceError('forbidden',403)
        if not key or len(key)>200 or not key.strip(): raise ServiceError('invalid_idempotency_key',422)
        digest=hashlib.sha256(json.dumps({'product_id':product_id,**payload.model_dump()},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        existing=self.repository.replay(principal.user_id,key)
        if existing is not None: return self._replayed(existing,digest)
        product=self.repository.product(product_id)
        if product is None: raise ServiceError('product_not_found',404)
        if payload.asin is not None and payload.asin not in self.repository.known_variants(product):
            raise ServiceError('invalid_variant',422)
        now=datetime.now(timezone.utc)
        if not self.repository.consume(principal.user_id,now,self.submission_limit):
            existing=self.repository.replay(principal.user_id,key)
            if existing is not None: return self._replayed(existing,digest)
            raise ServiceError('rate_limited',429)
        row=self.repository.create_submission(product_id,principal.user_id,key,payload,digest,now)
        if self.summaries is not None:
            try:
                self.summaries.admit_review(row)
                self.repository.database.reviews.update_one({'_id':row['_id']}, {'$set':{'summary_input_outstanding':False}})
            except Exception:
                # The saved review remains acknowledged. The summary worker's
                # reconciliation sweep will retry this durable marker.
                pass
        return self._replayed(row,digest)

    def _replayed(self,row,digest):
        if row['payload_digest']!=digest: raise ServiceError('idempotency_conflict',409)
        return {'id':row['_id'],'processing':row['processing'],
                'summary':self._summary_status(row)}

    def _summary_status(self,row):
        if self.summaries is None: return None
        result = self.summaries.summary_status(row['parent_asin'], row['_id'])
        if result['status'] == 'incorporated':
            return {'status':'included','version':result['version']}
        if result['status'] == 'untracked':
            return {'status':'saved','version':None}
        state = self.summaries.current(row['parent_asin']).status
        if state == 'uninitialized':
            return {'status':'needs_initial_summary','version':None}
        return {'status':state if state in {'queued','updating','failed'} else 'waiting','version':None}

    def status(self, review_id, principal):
        row=self.repository.get(review_id)
        if row is None: raise ServiceError('review_not_found',404)
        if principal.role!='pm' and row.get('author_id')!=principal.user_id: raise ServiceError('forbidden',403)
        return {'id':row['_id'],'processing':row.get('processing'),
                'summary':self._summary_status(row)}

    def list(self, product_id: str, principal: Principal, source: str | None, batch_id: str | None, cursor: str | None, limit: int,
             *, sentiment: str | None=None, rating: int | None=None, sort: str | None=None) -> dict:
        if not 1<=limit<=100: raise ServiceError('invalid_page_size',422)
        if self.repository.product(product_id) is None: raise ServiceError('product_not_found',404)
        if source not in (None,'amazon_2023','user_submission'): raise ServiceError('invalid_source',422)
        if sentiment not in (None,'positive','neutral','negative'): raise ServiceError('invalid_sentiment',422)
        if rating is not None and (type(rating) is not int or not 1<=rating<=5): raise ServiceError('invalid_rating',422)
        if sort not in (None,'priority','newest'): raise ServiceError('invalid_sort',422)
        author_id=principal.user_id if principal.role!='pm' else None
        feed_sort=sort or ('priority' if sentiment is not None or rating is not None else None)
        if feed_sort is None:
            after=cursor_decode(cursor) if cursor else None
            rows=self.repository.list(product_id,author_id,source,batch_id,after,limit)
            next_cursor=cursor_encode(rows[limit-1]['timestamp'],rows[limit-1]['_id']) if len(rows)>limit else None
        else:
            after=feed_cursor_decode(cursor,feed_sort,source,batch_id,sentiment,rating) if cursor else None
            rows=self.repository.feed(product_id,author_id,source,batch_id,sentiment,rating,feed_sort,after,limit)
            next_cursor=feed_cursor_encode(rows[limit-1],feed_sort,source,batch_id,sentiment,rating) if len(rows)>limit else None
        return {'items':[public_review(r) for r in rows[:limit]],'next_cursor':next_cursor}

    def products(self, cursor, limit):
        if not 1<=limit<=100: raise ServiceError('invalid_page_size',422)
        rows=self.repository.products(cursor,limit)
        return {'items':[self._public_product(r) for r in rows[:limit]],
                'next_cursor':rows[limit-1]['_id'] if len(rows)>limit else None}

    def product(self, product_id):
        row=self.repository.product(product_id)
        if row is None: raise ServiceError('product_not_found',404)
        return self._public_product(row)

    @staticmethod
    def _public_product(row):
        return {'id':row['_id'],'title':row['title'],'product_type':row.get('product_type')}
