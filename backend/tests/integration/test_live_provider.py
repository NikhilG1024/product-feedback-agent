"""Opt-in single synthetic Groq Free smoke; never substitutes a model."""
import os
import pytest
from app.domain import MemoryContext
from app.integrations.llm import GroqModel
from app.services.evidence import validate_findings


@pytest.mark.skipif(os.getenv('RUN_GROQ_FREE_SMOKE') != '1' or os.getenv('GROQ_FREE_CONFIRMED') != '1',
                   reason='Requires explicit live smoke opt-in and Groq Free account confirmation')
def test_live_groq_free_model_structured_response():
    key = os.getenv('LLM_API_KEY') or os.getenv('GROQ_API_KEY', '')
    assert key, 'Configure a server-side Groq key before opting in'
    model = GroqModel(key)
    reviews = [{'_id': 'synthetic', 'text': 'The hinge broke after one day.'}]
    try:
        findings = model.extract({'title': 'Synthetic chair'}, reviews, MemoryContext(text='', record_ids=[]))
        assert findings, 'Synthetic defect should produce grounded evidence'
        validate_findings(findings, reviews)
    finally:
        model.close()
