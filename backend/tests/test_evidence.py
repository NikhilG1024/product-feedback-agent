import pytest
from app.domain import FindingDraft, Evidence
from app.errors import ServiceError


def draft(review_id='r1', quote='The hinge broke.', theme='Hinge', issue_type='reported_defect'):
    return FindingDraft(issue_type=issue_type, theme=theme, description='Hinge reports', evidence=[Evidence(review_id=review_id, quote=quote)])


def test_citations_must_exist_and_be_verbatim():
    from app.services.evidence import validate_findings
    reviews=[{'_id':'r1','text':'The hinge broke.'}]
    for finding in (draft('outside'), draft(quote='The hinge exploded.'), draft(quote='')):
        with pytest.raises(ServiceError): validate_findings([finding], reviews)
    assert validate_findings([draft(), draft(theme=' HINGE ')],reviews)[0]['supporting_review_count']==1


def test_many_citations_keep_exact_counts_with_a_bounded_evidence_sample():
    from app.services.evidence import validate_findings
    reviews=[{'_id':str(i),'text':'The hinge broke.'} for i in range(30)]
    finding=validate_findings([draft(str(i)) for i in range(30)],reviews)[0]
    assert finding['supporting_review_count']==30
    assert len(finding['evidence'])==20
    assert finding['evidence_sampled'] is True


def test_verbatim_quote_does_not_claim_semantic_proof():
    from app.services.evidence import validate_findings
    finding=validate_findings([draft(quote='hinge broke')],[{'_id':'r1','text':'I never said the hinge broke.'}])[0]
    assert finding['provenance_validated'] is True
    assert finding['semantic_support']=='model_interpretation'


def test_same_normalized_theme_does_not_double_count_category_disagreement():
    from app.services.evidence import validate_findings
    result=validate_findings([draft(),draft(theme=' hinge ',issue_type='preference')],[{'_id':'r1','text':'The hinge broke.'}])
    assert len(result)==1
    assert result[0]['supporting_review_count']==1
    assert result[0]['issue_type']=='other'
