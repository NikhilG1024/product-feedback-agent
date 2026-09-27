"""Authoritative PM guidance and its durable memory intent."""
from app.repositories.capacity import CapacityGuard
from app.errors import ServiceError
from uuid import uuid4


class DecisionRepository:
    def __init__(self, database, *, capacity=None):
        self.database = database
        self.capacity = capacity if capacity is not None else CapacityGuard(database)

    def product(self, product_id): return self.database.products.find_one({'_id': product_id})

    def evidence(self, ids): return list(self.database.reviews.find({'_id': {'$in': ids}}).limit(101))

    def create(self, product_id, principal, payload, now):
        document = {'_id': str(uuid4()), 'parent_asin': product_id, 'kind': payload.kind,
                    'rationale': payload.rationale, 'evidence_ids': list(dict.fromkeys(payload.evidence_ids)),
                    'available_through': payload.available_through or now, 'decided_at': now,
                    'created_at': now, 'author_id': principal.user_id,
                    'processing': {'status': 'pending', 'attempts': 0, 'next_attempt_at': now, 'memory_status': 'pending'}}
        self.capacity.check_documents([document])
        self.database.decisions.insert_one(document)
        return document

    def by_ids(self, product_id, ids):
        if len(ids) > 100: raise ServiceError('narrower_guidance_scope_required', 422)
        rows = {d['_id']: d for d in self.database.decisions.find({'parent_asin': product_id, '_id': {'$in': ids}}).limit(100)}
        if set(rows) != set(ids): raise ServiceError('guidance_snapshot_unavailable', 409)
        return [rows[key] for key in ids]

    def list(self, product_id, limit):
        return list(self.database.decisions.find({'parent_asin': product_id}).sort([('decided_at', -1), ('_id', 1)]).limit(limit))

    def guidance_candidates(self, product_id, cutoff, after=None, limit=100):
        query = {'parent_asin': product_id, 'decided_at': {'$lte': cutoff},
                 'available_through': {'$lte': cutoff},
                 '$or': [{'created_at': {'$exists': False}}, {'created_at': {'$lte': cutoff}}]}
        if after is not None:
            stamp, record_id = after
            query['$and'] = [{'$or': [{'decided_at': {'$gt': stamp}},
                                     {'decided_at': stamp, '_id': {'$gt': record_id}}]}]
        return list(self.database.decisions.find(query).sort([('decided_at', 1), ('_id', 1)]).limit(limit))
