"""Validate frozen local candidates and stage them without publication."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from app.summaries.contracts import GeneratedSummary, SummaryVersion

B_CUTOFF = datetime(2022, 10, 27, 1, 13, 13, 233000, tzinfo=timezone.utc)

def sample_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str).encode()).hexdigest()

def _date(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result

@dataclass(frozen=True)
class PreparedArtifact:
    candidate: SummaryVersion
    normalized_bytes: bytes
    original_sha256: str
    model_manifest: dict
    reviews: list[dict]

def prepare_artifact(manifest, reviews, artifact_bytes, *, group):
    artifact = json.loads(artifact_bytes)
    product_id = artifact['product_id']
    matches = [p for p in manifest['products'] if p['_id'] == product_id]
    if len(matches) != 1:
        raise ValueError('product missing or duplicated in manifest')
    entry = matches[0]
    ids = [r['_id'] for r in reviews]
    expected = min(20, entry['eligible_count'])
    if (not expected or entry['sample_size'] != expected or len(ids) != expected or len(set(ids)) != expected or ids != entry['review_ids'] or ids != artifact['review_ids']):
        raise ValueError('sample membership mismatch')
    if sample_digest(reviews) != entry['sample_sha256'] or artifact['sample_sha256'] != entry['sample_sha256']:
        raise ValueError('sample content hash mismatch')
    cutoff = _date(manifest['filter']['timestamp']['$lte'])
    if cutoff > B_CUTOFF:
        raise ValueError('sample cutoff exceeds held-out boundary')
    for review in reviews:
        if (review['parent_asin'] != product_id or review.get('held_out') is not False or review.get('batch') not in {'A', 'B'} or review.get('source') == 'user_submission' or review['batch_id'] not in manifest['filter']['batch_id']['$in'] or not review['batch_id'].endswith(':' + review['batch']) or _date(review['timestamp']) > cutoff):
            raise ValueError('ineligible source review')
    aliases = {f'R{i:02d}': row['_id'] for i, row in enumerate(reviews, 1)}
    if artifact['alias_to_review_id'] != aliases:
        raise ValueError('alias membership mismatch')
    if artifact['status'] != 'citation_checks_passed':
        raise ValueError('candidate did not pass generation checks')
    by_id = {r['_id']: r for r in reviews}
    themes = []
    for i, theme in enumerate(artifact['summary']['themes'], 1):
        evidence = []
        for item in theme['evidence']:
            review_id = aliases[item['review_id']]
            quote = item['quote']
            if not quote.strip() or not any(quote in by_id[review_id][field] for field in ('title', 'text')):
                raise ValueError('quote does not match source')
            evidence.append({'review_id': review_id, 'quote': quote})
        themes.append({'id': 'initial-theme-' + str(i), 'description': theme['claim'], 'issue_type': theme['kind'], 'polarity': theme['polarity'], 'evidence': evidence})
    content = GeneratedSummary(narrative=artifact.get('narrative') or ' '.join(t['description'] for t in themes), themes=themes, contradictions=[])
    normalized = content.model_dump_json().encode()
    original_digest = hashlib.sha256(artifact_bytes).hexdigest()
    manifest_digest = sample_digest(manifest)
    model = dict(artifact.get('model_manifest') or {'identity': artifact['model'], 'weight_sha256': artifact['artifact_sha256']})
    model['import_provenance'] = {'original_artifact_sha256': original_digest, 'sample_manifest_sha256': manifest_digest,
        'sample_manifest': {k: v for k, v in manifest.items() if k != 'products'}, 'sample_entry': entry,
        'source_artifact': artifact, 'candidate_group': group, 'normalization': 'initialization-import-v1'}
    candidate = SummaryVersion(**content.model_dump(), product_id=product_id, version=1, parent_version=None,
        job_id='initial:' + group + ':' + original_digest, kind='initial',
        coverage={'historical_sample_count': len(ids), 'new_review_count': 0}, delta_review_ids=ids,
        manifest_ref='sha256:' + manifest_digest, model_identity=artifact['model'], prompt_version=artifact['prompt_version'],
        guidance_references=[], created_at=_date(artifact['created_at']))
    return PreparedArtifact(candidate, normalized, original_digest, model, reviews)


def stage_prepared(prepared, repository):
    """Admit frozen membership before staging; both operations are replay-safe."""
    repository.admit_initial_reviews(prepared.candidate.product_id,
        [dict(row, timestamp=_date(row['timestamp'])) for row in prepared.reviews])
    return repository.stage_initial_draft(prepared.candidate.product_id, prepared.candidate,
                                         prepared.normalized_bytes, prepared.model_manifest)


def verify_live_sources(prepared, database):
    """Validate the complete selected source set before any import write."""
    expected = {}
    for item in prepared:
        for row in item.reviews:
            if row['_id'] in expected and expected[row['_id']] != row:
                raise ValueError('conflicting frozen source')
            expected[row['_id']] = row
    actual = {r['_id']: r for r in database.reviews.find({'_id': {'$in': list(expected)}})}
    if actual.keys() != expected.keys():
        raise ValueError('live source missing')
    for review_id, frozen in expected.items():
        live = actual[review_id]
        if live.get('source') == 'user_submission':
            raise ValueError('live source is not historical')
        projected = {key: live.get(key) for key in frozen}
        if sample_digest(projected) != sample_digest(frozen):
            raise ValueError('live source differs from frozen sample: ' + review_id)
    products = {p.candidate.product_id for p in prepared}
    if database.products.count_documents({'_id': {'$in': list(products)}}) != len(products):
        raise ValueError('live product missing')


def load_artifacts(manifest_path, reviews_path, artifact_dirs, rejection_path=None):
    from pathlib import Path
    manifest = json.loads(Path(manifest_path).read_bytes())
    snapshots = [json.loads(line) for line in Path(reviews_path).read_text().splitlines() if line.strip()]
    by_product = {row['product_id']: row['reviews'] for row in snapshots}
    if len(by_product) != len(snapshots):
        raise ValueError('duplicate source product')
    rejections = json.loads(Path(rejection_path).read_bytes()) if rejection_path else {}
    prepared = []
    jobs = set()
    for directory in artifact_dirs:
        folder = Path(directory)
        paths = sorted(folder.glob('*.json'))
        if not paths:
            raise ValueError('empty artifact directory')
        for path in paths:
            raw = path.read_bytes()
            artifact = json.loads(raw)
            item = prepare_artifact(manifest, by_product[artifact['product_id']], raw, group=folder.name)
            if item.candidate.job_id in jobs:
                raise ValueError('duplicate artifact job')
            jobs.add(item.candidate.job_id)
            if item.original_sha256 in rejections:
                audit = rejections[item.original_sha256]
                if audit.get('status') != 'rejected':
                    raise ValueError('import accepts rejection records only; approvals require separate review')
                item.model_manifest['import_provenance']['semantic_rejection'] = audit
            prepared.append(item)
    return prepared


def apply_rejection(prepared, repository, version_id):
    audit = prepared.model_manifest['import_provenance'].get('semantic_rejection')
    if audit is None:
        return
    review = {'status': 'rejected', 'reviewer_id': audit['reviewer_id'],
              'reviewer_type': audit['reviewer_type'], 'reviewed_at': _date(audit['reviewed_at']),
              'artifact_sha256': hashlib.sha256(prepared.normalized_bytes).hexdigest(),
              'rubric_version': audit['rubric_version']}
    # Missing rubric assessments stay absent; rejection must not invent results.
    for key in ('factual_support', 'coverage', 'classification'):
        if key in audit:
            review[key] = audit[key]
    repository.apply_semantic_review(prepared.candidate.product_id, version_id, review)


def main():
    import argparse
    from pathlib import Path
    from pymongo import MongoClient
    from app.config import Settings
    from app.repositories.capacity import CapacityGuard
    from app.summaries.repository import SummaryRepository
    parser = argparse.ArgumentParser(description='Stage verified local initialization drafts; never publishes')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--reviews', required=True)
    parser.add_argument('--artifacts', action='append', required=True)
    parser.add_argument('--rejections')
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true', help='Write staged drafts; default validates only')
    args = parser.parse_args()
    prepared = load_artifacts(args.manifest, args.reviews, args.artifacts, args.rejections)
    settings = Settings.from_env()
    with MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=10000) as client:
        db = client[settings.mongo_database]
        verify_live_sources(prepared, db)
        CapacityGuard(db).check_documents([p.model_manifest for p in prepared])
        report = {'mode': 'apply' if args.apply else 'dry-run', 'candidates': len(prepared),
                  'products': len({p.candidate.product_id for p in prepared}),
                  'selected_reviews': len({r['_id'] for p in prepared for r in p.reviews}),
                  'published_by_importer': 0, 'versions': []}
        repo = SummaryRepository(db)
        if args.apply:
            for item in prepared:
                version_id = stage_prepared(item, repo)
                apply_rejection(item, repo, version_id)
                stored = repo.draft(version_id)
                if stored is None or stored.narrative != item.candidate.narrative or stored.delta_review_ids != item.candidate.delta_review_ids:
                    raise ValueError('stored version readback mismatch')
                report['versions'].append({'product_id': item.candidate.product_id, 'version_id': version_id,
                    'job_id': item.candidate.job_id, 'original_artifact_sha256': item.original_sha256,
                    'semantic_status': stored.semantic_review.status})
                if len(report['versions']) % 25 == 0:
                    print(json.dumps({'staged': len(report['versions']), 'target': len(prepared)}), flush=True)
        target = Path(args.report)
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(report, indent=2) + '\n')
        temp.replace(target)
        print(json.dumps({k: v for k, v in report.items() if k != 'versions'}))


if __name__ == '__main__':
    main()
