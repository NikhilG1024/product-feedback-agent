import json

import httpx
import pytest

from app.integrations.memory import ProviderError
from app.integrations.openrouter_summary import (
    OPENROUTER_SUMMARY_MODEL, OPENROUTER_SUMMARY_URL, OpenRouterSummaryModel)
from app.summaries.contracts import GeneratedSummary
from app.summaries.generation import _prompt_messages


MESSAGES = _prompt_messages({"id": "P", "title": "Headphones", "product_type": None}, None,
    [{"id": "r1", "title": "Battery", "text": "Battery lasts all day.", "rating": 5}], [])
OUTPUT = {"narrative": "Customers report long battery life.", "themes": [{
    "id": "battery", "description": "Long battery life", "issue_type": "other",
    "polarity": "positive", "evidence": [{"review_id": "r1", "quote": "Battery lasts all day."}]}],
    "contradictions": []}


def completion(content, reason="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": reason,
        "message": {"content": content}}]})


def test_exact_free_ultra_request_and_locally_validated_json():
    requests = []
    def respond(request):
        requests.append(request)
        return completion(json.dumps(OUTPUT))
    model = OpenRouterSummaryModel("secret", transport=httpx.MockTransport(respond))
    assert model.generate_summary(MESSAGES, GeneratedSummary) == OUTPUT
    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == OPENROUTER_SUMMARY_URL + "/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert payload["model"] == OPENROUTER_SUMMARY_MODEL == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert payload["messages"] == MESSAGES
    assert payload["stream"] is False
    assert payload["reasoning"] == {"enabled": False}
    assert payload["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    assert "response_format" not in payload
    assert "models" not in payload and "route" not in payload


@pytest.mark.parametrize("changes", [
    {"model": "nvidia/nemotron-3-super-120b-a12b:free"},
    {"model": "nvidia/nemotron-3-ultra-550b-a55b"},
    {"base_url": "https://api.groq.com/openai/v1"},
    {"base_url": "https://openrouter.ai/api/v1/evil"},
])
def test_adapter_rejects_other_models_or_endpoints(changes):
    with pytest.raises(ValueError, match="free"):
        OpenRouterSummaryModel("secret", **changes)


def test_missing_key_fails_before_network():
    with pytest.raises(ProviderError, match="provider_not_configured"):
        OpenRouterSummaryModel("", transport=httpx.MockTransport(lambda _: pytest.fail("network")))


@pytest.mark.parametrize("status,code", [(429, "model_rate_limited"), (401, "provider_not_configured"),
                                          (503, "model_provider_failed")])
def test_provider_errors_are_sanitized_and_never_fallback(status, code):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "secret private data"}})
    model = OpenRouterSummaryModel("secret", transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError, match=f"^{code}$"):
        model.generate_summary(MESSAGES, GeneratedSummary)
    assert len(calls) == 1


@pytest.mark.parametrize("content,reason", [
    ("not json", "stop"), (json.dumps(OUTPUT), "length"),
    (json.dumps({**OUTPUT, "narrative": ""}), "stop"),
])
def test_malformed_incomplete_or_invalid_output_never_publishes(content, reason):
    model = OpenRouterSummaryModel("secret", transport=httpx.MockTransport(
        lambda _: completion(content, reason)))
    with pytest.raises(ProviderError, match="model_invalid_output"):
        model.generate_summary(MESSAGES, GeneratedSummary)


def test_multibyte_full_request_budget_rejects_without_truncation_or_network():
    model = OpenRouterSummaryModel("secret", transport=httpx.MockTransport(
        lambda _: pytest.fail("oversized request reached network")))
    huge = _prompt_messages({"id": "P", "title": "T", "product_type": None}, None,
        [{"id": "r1", "title": "T", "text": "🙂" * 18000, "rating": 5}], [])
    with pytest.raises(ProviderError, match="model_input_too_large"):
        model.generate_summary(huge, GeneratedSummary)
    assert "🙂" * 18000 in huge[1]["content"]
