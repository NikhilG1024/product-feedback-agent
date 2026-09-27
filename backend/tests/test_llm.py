import json
import httpx
import pytest

from app.domain import MemoryContext
from app.integrations.llm import DeepSeekModel
from app.integrations.memory import ProviderError


FINDING = {'issue_type': 'reported_defect', 'theme': 'battery life', 'description': 'Battery lasts two hours.',
           'evidence': [{'review_id': 'review-1', 'quote': 'Battery lasts two hours.'}]}
REVIEWS = [{'id': 'review-1', 'text': 'Battery lasts two hours.'}]
CONTEXT = MemoryContext(text='', record_ids=[])


def completion(content):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})


def test_extract_returns_validated_findings_using_groq_json_contract():
    def provider(request):
        body = json.loads(request.content)
        assert body['model'] == 'openai/gpt-oss-20b'
        assert body['response_format'] == {'type': 'json_object'}
        assert body['max_tokens'] <= 1536
        assert request.headers['Authorization'] == 'Bearer secret'
        assert 'JSON' in body['messages'][0]['content']
        assert request.url == 'https://api.groq.com/openai/v1/chat/completions'
        return completion(json.dumps({'findings': [FINDING]}))
    model = DeepSeekModel('secret', transport=httpx.MockTransport(provider))
    findings = model.extract({'parent_asin': 'P1'}, REVIEWS, CONTEXT)
    assert findings[0].issue_type == 'reported_defect'
    assert findings[0].evidence[0].review_id == 'review-1'



@pytest.mark.parametrize('output', ['not json', '', '{"findings":[]',
    json.dumps({'findings': [{**FINDING, 'issue_type': 'confirmed_defect'}]}),
    json.dumps({'findings': [{**FINDING, 'evidence': []}]}),
    json.dumps({'findings': [{**FINDING, 'theme': ''}]}),
    json.dumps({'findings': [], 'unexpected': True})])
def test_malformed_or_unsupported_model_output_is_rejected(output):
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request: completion(output)))
    with pytest.raises(ProviderError, match='^model_invalid_output$'):
        model.extract({}, REVIEWS, CONTEXT)


def test_provider_errors_are_sanitized():
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request:
        httpx.Response(500, json={'error': 'secret and sensitive review text'})))
    with pytest.raises(ProviderError, match='^model_provider_failed$'):
        model.extract({}, REVIEWS, CONTEXT)


def test_over_budget_review_is_rejected_before_network():
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request: pytest.fail('Must reject before provider')))
    with pytest.raises(ProviderError, match='^model_input_too_large$'):
        model.extract({}, [{'id': 'r1', 'text': 'x' * 40001}], CONTEXT)


def test_answer_returns_structured_citations():
    expected = {'answer': 'The battery lasts two hours.', 'evidence': FINDING['evidence'], 'insufficient_evidence': False}
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request: completion(json.dumps(expected))))
    assert model.answer('How long does the battery last?', [FINDING], FINDING['evidence']) == expected


@pytest.mark.parametrize('output', [
    {'answer': 'The battery lasts two hours.', 'evidence': [], 'insufficient_evidence': False},
    {'answer': 'Unknown.', 'evidence': [], 'insufficient_evidence': 'yes'},
    {'answer': 'It explodes.', 'evidence': [{'review_id': 'invented', 'quote': 'It explodes.'}], 'insufficient_evidence': False},
])
def test_answer_rejects_unstructured_or_unsupported_citations(output):
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request: completion(json.dumps(output))))
    with pytest.raises(ProviderError, match='^model_invalid_output$'):
        model.answer('How long?', [FINDING], FINDING['evidence'])


@pytest.mark.parametrize('finish_reason', ['length', 'content_filter', 'tool_calls'])
def test_truncated_or_non_answer_completion_cannot_publish(finish_reason):
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices': [{'finish_reason': finish_reason, 'message': {'content': json.dumps({'findings': [FINDING]})}}]})))
    with pytest.raises(ProviderError, match='^model_invalid_output$'):
        model.extract({}, REVIEWS, CONTEXT)


def test_groq_requires_credentials_before_network():
    with pytest.raises(ProviderError, match='provider_not_configured'):
        DeepSeekModel('', transport=httpx.MockTransport(lambda request: pytest.fail('No anonymous call')))


def test_other_transports_still_require_credentials():
    from app.integrations.http import JSONTransport
    with pytest.raises(ProviderError, match='provider_not_configured'):
        JSONTransport('https://memory.example','')



@pytest.mark.parametrize('changes', [
    {'model': 'deepseek-v4-flash'}, {'model': 'deepseek-v4-pro-free'},
    {'base_url': 'https://api.deepseek.com'},
    {'base_url': 'https://opencode.ai.evil.test/zen/v1'},
    {'base_url': 'http://opencode.ai/zen/v1'},
])
def test_free_only_adapter_rejects_unapproved_model_or_endpoint(changes):
    with pytest.raises(ValueError, match='free'):
        DeepSeekModel('secret', **changes)


@pytest.mark.parametrize('status', [429, 503])
def test_unavailable_free_model_never_falls_back(status):
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(status, json={'error':'unavailable'})
    with pytest.raises(ProviderError, match='model_rate_limited' if status == 429 else 'model_provider_failed'):
        DeepSeekModel('secret', transport=httpx.MockTransport(provider)).extract({}, REVIEWS, CONTEXT)
    assert len(requests) == 1
    assert json.loads(requests[0].content)['model'] == 'openai/gpt-oss-20b'


def test_settings_enforce_free_defaults_and_reject_paid_environment(monkeypatch):
    from app.config import Settings
    for key, value in {'MONGODB_URI':'mongodb://localhost', 'MONGODB_DATABASE':'test_free',
                       'DEMO_REVIEWER_TOKEN':'reviewer', 'DEMO_PM_TOKEN':'pm'}.items():
        monkeypatch.setenv(key,value)
    for key in ['LLM_API_URL','LLM_MODEL','LLM_API_KEY','GROQ_API_KEY']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY','unrelated-secret')
    monkeypatch.setenv('OPENCODE_API_KEY','legacy-secret')
    settings = Settings.from_env()
    assert settings.llm_model == 'openai/gpt-oss-20b'
    assert settings.llm_api_url == 'https://api.groq.com/openai/v1'
    assert settings.llm_api_key == ''
    monkeypatch.setenv('LLM_MODEL','paid-model')
    with pytest.raises(ValueError, match='free'):
        Settings.from_env()


def test_groq_key_selected_with_explicit_llm_override(monkeypatch):
    from app.config import Settings
    for key, value in {'MONGODB_URI':'mongodb://localhost', 'MONGODB_DATABASE':'test_free',
                       'DEMO_REVIEWER_TOKEN':'reviewer', 'DEMO_PM_TOKEN':'pm',
                       'GROQ_API_KEY':'groq-secret'}.items():
        monkeypatch.setenv(key, value)
    for key in ['LLM_API_URL','LLM_MODEL','LLM_API_KEY']:
        monkeypatch.delenv(key, raising=False)
    assert Settings.from_env().llm_api_key == 'groq-secret'
    monkeypatch.setenv('LLM_API_KEY', 'explicit-groq-secret')
    assert Settings.from_env().llm_api_key == 'explicit-groq-secret'


def test_complete_prompt_budget_rejects_multibyte_context_without_network():
    model = DeepSeekModel('secret', transport=httpx.MockTransport(lambda request: pytest.fail('Oversized prompt')))
    with pytest.raises(ProviderError, match='model_input_too_large'):
        model.extract({}, REVIEWS, MemoryContext(text='😀' * 2000, record_ids=[]))


def test_default_chunks_cover_every_review_without_truncation():
    from app.config import Settings
    from app.services.analysis import AnalysisService
    config = Settings('mongodb://localhost', 'test_groq', 'reviewer', 'pm')
    service = AnalysisService.__new__(AnalysisService)
    service.max_chunk_reviews, service.max_chunk_chars = config.max_chunk_reviews, config.max_chunk_chars
    reviews = [{'text': str(i) * 500, 'id': str(i)} for i in range(9)]
    chunks = service.chunks(reviews)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 5 and sum(len(r['text']) for r in chunk) <= 3000 for chunk in chunks)
    assert [r for chunk in chunks for r in chunk] == reviews


def test_extraction_omits_storage_metadata_without_truncating_review():
    captured = []
    def respond(request):
        captured.append(json.loads(request.content))
        return completion(json.dumps({'findings': []}))
    model = DeepSeekModel('secret', transport=httpx.MockTransport(respond))
    review = {'_id': 'r1', 'title': 'Battery', 'text': 'Battery lasts two hours.',
              'rating': 2, 'provenance': {'source': 'x' * 8000}, 'processing': {'internal': True}}
    model.extract({'_id': 'p1', 'title': 'Headphones'}, [review], CONTEXT)
    sent = json.loads(captured[0]['messages'][1]['content'])['reviews'][0]
    assert sent == {k: review[k] for k in ('_id', 'title', 'text', 'rating')}
    assert review['provenance']['source'] == 'x' * 8000
