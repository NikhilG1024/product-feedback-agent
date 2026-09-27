"""Separate mechanical provenance and hand-labeled semantic support.

Changing provenance validation into a semantic guarantee must fail this test.
The labels score these synthetic candidate claims, not any live model's quality.
"""
import json
from pathlib import Path
from app.domain import FindingDraft, Evidence
from app.services.evidence import validate_findings


def test_verbatim_unsupported_claims_are_flagged_by_separate_labeled_evaluation():
    cases=json.loads((Path(__file__).parent/'fixtures/semantic_cases.json').read_text())['cases']
    provenance_passed=[]
    semantic_flags=[]
    for case in cases:
        draft=FindingDraft(issue_type='reported_defect',theme='hinge',description=case['claim'],
            evidence=[Evidence(review_id=case['id'],quote=case['quote'])])
        finding=validate_findings([draft],[{'_id':case['id'],'text':case['text']}])[0]
        assert finding['semantic_support']=='model_interpretation'
        provenance_passed.append(finding['provenance_validated'])
        if not case['label']['supported']:
            semantic_flags.append({'case':case['id'],'reason':case['label']['reason']})
    assert provenance_passed==[True,True,True]
    assert [item['case'] for item in semantic_flags]==['negation-omitted','cause-invented']
    assert all(item['reason'] for item in semantic_flags)
    # Two independent metrics with an explicit synthetic denominator of three.
    assert sum(provenance_passed)==3
    assert sum(case['label']['supported'] for case in cases)==1
