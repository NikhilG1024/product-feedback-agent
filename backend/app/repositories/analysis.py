"""Immutable analysis inputs and attempt-isolated result publication."""
from app.repositories.capacity import CapacityGuard
import hashlib
import json
from uuid import uuid4
from pymongo import UpdateOne
from app.errors import ServiceError


class AnalysisRepository:
    def __init__(self, database, *, capacity=None):
        self.database = database
        self.capacity = capacity if capacity is not None else CapacityGuard(database)

    def product(self, product_id):
        return self.database.products.find_one({'_id': product_id}, {'title': 1, 'product_type': 1})

    def reviews(self, product_id, scope, cutoff, limit, *, review_ids=None):
        query = {'parent_asin': product_id, 'timestamp': {'$lte': cutoff}}
        if review_ids is not None: query['_id'] = {'$in': review_ids}
        clauses = [{'$or': [{'created_at': {'$exists': False}}, {'created_at': {'$lte': cutoff}}]},
                   {'$or': [{'available_at': {'$exists': False}}, {'available_at': {'$lte': cutoff}}]}]
        if scope['source'] == 'amazon_2023':
            clauses.append({'$or': [{'source': 'amazon_2023'}, {'source': {'$exists': False}}]})
        else: query['source'] = scope['source']
        if scope.get('batch_id'): query['batch_id'] = scope['batch_id']
        if not scope.get('evaluation'):
            query.update(held_out={'$ne': True}, batch={'$ne': 'C'})
        query['$and'] = clauses
        sample_limit = min(limit, scope.get('sample_size') or limit)
        return list(self.database.reviews.find(query).sort([('timestamp', 1), ('_id', 1)]).limit(sample_limit))

    def batch(self, batch_id): return self.database.batches.find_one({'_id': batch_id})

    def eligible_decisions(self, product_id, cutoff, review_ids, limit=101):
        # Both server creation and declared availability must precede the replay cutoff.
        query = {'parent_asin': product_id, 'decided_at': {'$lte': cutoff}, 'available_through': {'$lte': cutoff},
                 '$and': [{'$or': [{'created_at': {'$exists': False}}, {'created_at': {'$lte': cutoff}}]},
                          {'$or': [{'evidence_ids': {'$exists': False}}, {'evidence_ids': {'$not': {'$elemMatch': {'$nin': review_ids}}}}]}]}
        return list(self.database.decisions.find(query).sort([('decided_at', 1), ('_id', 1)]).limit(limit))

    def create(self, product, scope, mode, reviews, decisions, now, cutoff):
        run_id = str(uuid4())
        material = {'reviews': reviews, 'decisions': decisions, 'scope': scope, 'product': product}
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
        # Separate documents avoid Mongo's per-document ceiling for 1,500 Unicode reviews.
        snapshots = [{'_id': f'{run_id}:review:{i}', 'run_id': run_id, 'kind': 'review', 'position': i, 'value': r}
                     for i, r in enumerate(reviews)]
        snapshots += [{'_id': f'{run_id}:decision:{i}', 'run_id': run_id, 'kind': 'decision', 'position': i, 'value': d}
                      for i, d in enumerate(decisions)]
        run = {'_id': run_id, 'parent_asin': product['_id'], 'product': product, 'scope': scope, 'mode': mode,
               'created_at': now, 'available_through': cutoff, 'snapshot_hash': digest,
               'review_ids': [r['_id'] for r in reviews], 'decision_ids': [d['_id'] for d in decisions],
               'denominator': len(reviews), 'status': 'pending',
               'processing': {'status': 'pending', 'attempts': 0, 'next_attempt_at': now}}
        self.capacity.check_documents([run, *snapshots])
        if snapshots: self.database.analysis_snapshots.insert_many(snapshots)
        self.database.analysis_runs.insert_one(run)
        return run

    def get(self, run_id): return self.database.analysis_runs.find_one({'_id': run_id})

    @staticmethod
    def require_readable(run):
        required = {'scope', 'review_ids', 'decision_ids', 'snapshot_hash', 'denominator'}
        if not required <= run.keys() or (run['status'] == 'completed' and 'publication_token' not in run):
            raise ServiceError('legacy_analysis_unsupported', 409)

    def snapshot(self, run_id, kind):
        limit = 1500 if kind == 'review' else 100
        return [item['value'] for item in self.database.analysis_snapshots.find({'run_id': run_id, 'kind': kind}).sort('position', 1).limit(limit)]

    def save_extraction(self, run_id, review_ids, drafts):
        # Immutable documents are linked only by a successful lease-fenced checkpoint.
        # Failed/stale attempts may leave orphans, but cannot replace a linked result.
        identifier = uuid4().hex
        document = {'_id': identifier, 'run_id': run_id, 'kind': 'extraction_chunk',
                    'review_ids': review_ids, 'drafts': [item.model_dump() for item in drafts]}
        self.capacity.check_documents([document])
        self.database.analysis_outputs.insert_one(document)
        return identifier

    def extraction(self, run_id, identifier):
        return self.database.analysis_outputs.find_one(
            {'_id': identifier, 'run_id': run_id, 'kind': 'extraction_chunk'})

    def stage_findings(self, run, token, findings):
        self.capacity.check_documents(findings)
        operations = []
        for finding in findings:
            document = {**finding, '_id': token + ':' + finding['id'], 'analysis_run_id': run['_id'],
                        'parent_asin': run['parent_asin'], 'publication_token': token}
            operations.append(UpdateOne({'_id': document['_id']}, {'$setOnInsert': document}, upsert=True))
        if operations: self.database.findings.bulk_write(operations)

    def findings(self, run):
        self.require_readable(run)
        return list(self.database.findings.find({'analysis_run_id': run['_id'], 'publication_token': run['publication_token']}).sort([('supporting_review_count', -1), ('theme', 1)]))

    def stage_output(self, run_id, token, output):
        self.capacity.check_documents([output])
        self.database.analysis_outputs.update_one({'_id': token}, {'$setOnInsert': {'run_id': run_id, **output}}, upsert=True)

    def output(self, run):
        self.require_readable(run)
        return self.database.analysis_outputs.find_one({'_id': run['publication_token'], 'run_id': run['_id']}) or {}
