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
        return {'status':state if state in {'queued','updating','failed'} else 'waiting','version':None}

    def status(self, review_id, principal):
        row=self.repository.get(review_id)
        if row is None: raise ServiceError('review_not_found',404)
        if principal.role!='pm' and row.get('author_id')!=principal.user_id: raise ServiceError('forbidden',403)
        return {'id':row['_id'],'processing':row.get('processing'),
                'summary':self._summary_status(row)}

    def list(self, product_id: str, principal: Principal, source: str | None, batch_id: str | None, cursor: str | None, limit: int) -> dict:
        if not 1<=limit<=100: raise ServiceError('invalid_page_size',422)
        if self.repository.product(product_id) is None: raise ServiceError('product_not_found',404)
        if source not in (None,'amazon_2023','user_submission'): raise ServiceError('invalid_source',422)
        author_id=principal.user_id if principal.role!='pm' else None
        after=cursor_decode(cursor) if cursor else None
        rows=self.repository.list(product_id,author_id,source,batch_id,after,limit)
        next_cursor=cursor_encode(rows[limit-1]['timestamp'],rows[limit-1]['_id']) if len(rows)>limit else None
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
