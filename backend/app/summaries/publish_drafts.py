"""Publish existing initial drafts under an explicit operator acceptance policy."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pymongo import MongoClient
from app.config import Settings
from app.summaries.contracts import SemanticReview
from app.summaries.repository import SummaryRepository, artifact_sha256


def publish_all(database, *, apply=False):
    products = [row['_id'] for row in database.products.find({}, {'_id': 1})]
    def process(product_id):
        repo = SummaryRepository(database)
        state = database.product_summary_state.find_one({'_id': product_id})
        if state and state.get('current_version') is not None:
            return {'product_id': product_id, 'result': 'already_published'}
        candidate = database.product_summary_versions.find_one({
            'product_id': product_id, 'kind': 'initial', 'parent_version': None,
            'published_at': None, 'semantic_review.status': {'$in': ['pending', 'accepted', 'approved']}},
            sort=[('version', -1)])
        if candidate is None:
            return {'product_id': product_id, 'result': 'no_usable_draft'}
        if not apply:
            return {'product_id': product_id, 'result': 'would_publish', 'version': candidate['version']}
        now = datetime.now(timezone.utc)
        if candidate['semantic_review']['status'] == 'pending':
            acceptance = SemanticReview(status='accepted',
                reviewer_id='operator-authorized-draft-publication', reviewer_type='automated',
                reviewed_at=now, artifact_sha256=candidate.get('raw_artifact_sha256') or artifact_sha256(candidate),
                rubric_version='accept-existing-draft-without-semantic-validation-v1')
            if not repo.apply_semantic_review(product_id, candidate['_id'], acceptance):
                return {'product_id': product_id, 'result': 'acceptance_conflict'}
        claim = repo.claim_initial_candidate(product_id, candidate['_id'], now, 180)
        if not claim or not repo.publish(claim, candidate['_id'], datetime.now(timezone.utc)):
            return {'product_id': product_id, 'result': 'publication_conflict'}
        return {'product_id': product_id, 'result': 'published', 'version': candidate['version']}
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(process, products))
    counts = {}
    for row in results:
        counts[row['result']] = counts.get(row['result'], 0) + 1
    return {'counts': counts, 'products': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--accept-drafts', action='store_true',
                        help='Accept draft contents as initial summaries without claiming semantic validation')
    args = parser.parse_args()
    if args.apply and not args.accept_drafts:
        parser.error('--apply requires --accept-drafts')
    settings = Settings.from_env()
    with MongoClient(settings.mongo_uri, tz_aware=True) as client:
        print(json.dumps(publish_all(client[settings.mongo_database], apply=args.apply)))


if __name__ == '__main__':
    main()
