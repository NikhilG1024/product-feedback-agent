"""Pinned Groq Free-plan summary provider with bounded JSON output."""

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import FREE_LLM_MODEL, FREE_LLM_URL, validate_free_llm
from app.integrations.http import JSONTransport, ProviderHTTPError
from app.integrations.memory import ProviderError
from app.integrations.openrouter_summary import (
    EVIDENCE_LIMIT_REMINDER, _compact_representative_evidence)
from app.summaries.generation import serialized_prompt_bytes

SUMMARY_PROMPT_BUDGET_BYTES = 20000
MAX_SUMMARY_REQUEST_BYTES = 24000
MAX_SUMMARY_OUTPUT_BYTES = 100000
MAX_SUMMARY_COMPLETION_TOKENS = 3500
GROQ_COMPACT_REMINDER = (
    "\nKeep the JSON concise: use one representative quote per theme unless more are "
    "needed to cite every new review. Retain all prior theme IDs and required new "
    "review citations, but avoid repeating old citations across themes."
)


class GroqSummaryModel:
    """Separate summary client; legacy extraction limits remain independent."""

    summary_prompt_budget_bytes = (SUMMARY_PROMPT_BUDGET_BYTES -
                                   len((EVIDENCE_LIMIT_REMINDER +
                                        GROQ_COMPACT_REMINDER).encode("utf-8")))

    def __init__(self, api_key: str, *, base_url: str = FREE_LLM_URL,
                 model: str = FREE_LLM_MODEL, timeout: float = 240,
                 transport: Any = None):
        validate_free_llm(base_url, model)
        self.model = model
        self.http = JSONTransport(base_url, api_key, timeout=timeout,
            max_request_bytes=MAX_SUMMARY_REQUEST_BYTES,
            max_response_bytes=MAX_SUMMARY_OUTPUT_BYTES + 4096, transport=transport)

    def close(self) -> None:
        self.http.close()

    def generate_summary(self, messages: list[dict[str, str]], output_schema: type[BaseModel]) -> dict:
        if (not isinstance(messages, list) or len(messages) != 2
                or any(not isinstance(item, dict) for item in messages)
                or [item.get("role") for item in messages] != ["system", "user"]
                or any(not isinstance(item.get("content"), str) for item in messages)):
            raise ProviderError("model_invalid_input")
        try:
            bounded_messages = [{**messages[0], "content": messages[0]["content"] +
                                 EVIDENCE_LIMIT_REMINDER + GROQ_COMPACT_REMINDER}, messages[1]]
            prompt_size = serialized_prompt_bytes(bounded_messages)
            payload = {"model": self.model, "messages": bounded_messages,
                       "stream": False, "max_completion_tokens": MAX_SUMMARY_COMPLETION_TOKENS,
                       "response_format": {"type": "json_object"},
                       "reasoning_effort": "low", "include_reasoning": False}
            request_size = len(json.dumps(payload, ensure_ascii=False,
                                          allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, AttributeError):
            raise ProviderError("model_invalid_input") from None
        if prompt_size > SUMMARY_PROMPT_BUDGET_BYTES or request_size > MAX_SUMMARY_REQUEST_BYTES:
            raise ProviderError("model_input_too_large")
        try:
            response = self.http.request("POST", "/chat/completions", payload=payload)
        except ProviderHTTPError as exc:
            code = ("model_rate_limited" if exc.status == 429 else
                    "provider_not_configured" if exc.status in (401, 403) else
                    "model_provider_failed")
            raise ProviderError(code) from None
        except ProviderError:
            raise ProviderError("model_provider_failed") from None
        try:
            choice = response["choices"][0]
            if choice["finish_reason"] == "length":
                raise ProviderError("model_output_truncated")
            if choice["finish_reason"] != "stop":
                raise ValueError("Incomplete generation")
            content = choice["message"]["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SUMMARY_OUTPUT_BYTES:
                raise ValueError("Invalid content")
            fresh_ids = {str(row["id"]) for row in json.loads(messages[1]["content"])["new_reviews"]}
            parsed = _compact_representative_evidence(json.loads(content), fresh_ids)
            return output_schema.model_validate(parsed).model_dump()
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise ProviderError("model_invalid_output") from None
