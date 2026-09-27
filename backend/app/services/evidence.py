"""Provenance validation and deterministic counts; semantic support remains model judgment."""
import re
import unicodedata
from app.errors import ServiceError


def theme_key(theme):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', theme).strip().casefold())


def validate_findings(drafts, reviews):
    eligible = {str(review['_id']): review for review in reviews}
    grouped = {}
    for draft in drafts:
        key = theme_key(draft.theme)
        if not key or draft.issue_type not in {'reported_defect', 'preference', 'feature_request', 'other'} or not draft.evidence:
            raise ServiceError('invalid_finding', 422)
        group_key = key
        group = grouped.setdefault(group_key, {'issue_type': draft.issue_type, 'theme': key,
            'description': draft.description, 'evidence': [], 'review_ids': []})
        if group['issue_type'] != draft.issue_type: group['issue_type'] = 'other'
        for citation in draft.evidence:
            review = eligible.get(citation.review_id)
            if review is None or not citation.quote.strip() or citation.quote not in review['text']:
                raise ServiceError('invalid_evidence', 422)
            pair = {'review_id': citation.review_id, 'quote': citation.quote}
            if pair not in group['evidence']:
                group['evidence'].append(pair)
            if citation.review_id not in group['review_ids']:
                group['review_ids'].append(citation.review_id)
    for group in grouped.values():
        group['review_ids'].sort()
        group['evidence'].sort(key=lambda pair: (pair['review_id'], pair['quote']))
        group['evidence_sampled'] = len(group['evidence']) > 20
        group['evidence'] = group['evidence'][:20]
        group['supporting_review_count'] = len(group['review_ids'])
        group['provenance_validated'] = True
        group['semantic_support'] = 'model_interpretation'
    return sorted(grouped.values(), key=lambda group: (-group['supporting_review_count'], group['theme'], group['issue_type']))
