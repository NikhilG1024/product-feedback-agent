"""Free-only OpenRouter Nemotron summary adapter with local JSON validation."""

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.integrations.http import JSONTransport, ProviderHTTPError
from app.integrations.memory import ProviderError
from app.summaries.generation import serialized_prompt_bytes


OPENROUTER_SUMMARY_URL = "https://openrouter.ai/api/v1"
OPENROUTER_SUMMARY_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
MAX_SUMMARY_REQUEST_BYTES = 65536
SUMMARY_PROMPT_BUDGET_BYTES = 64000
MAX_SUMMARY_OUTPUT_BYTES = 100000


class OpenRouterSummaryModel:
    """Pinned free endpoint; no alternate model or charged provider route."""

    summary_prompt_budget_bytes = SUMMARY_PROMPT_BUDGET_BYTES

    def __init__(self, api_key: str, *, base_url: str = OPENROUTER_SUMMARY_URL,
                 model: str = OPENROUTER_SUMMARY_MODEL, timeout: float = 240,
                 transport: Any = None):
        if base_url.rstrip("/") != OPENROUTER_SUMMARY_URL or model != OPENROUTER_SUMMARY_MODEL:
            raise ValueError("Only the approved free OpenRouter summary endpoint and model are allowed")
        self.model = model
        self.http = JSONTransport(base_url, api_key, timeout=timeout,
            max_request_bytes=MAX_SUMMARY_REQUEST_BYTES,
            max_response_bytes=MAX_SUMMARY_OUTPUT_BYTES + 4096, transport=transport)

    def close(self) -> None:
        self.http.close()

    def generate_summary(self, messages: list[dict[str, str]], output_schema: type[BaseModel]) -> dict:
        if (not isinstance(messages, list) or len(messages) != 2
                or [item.get("role") for item in messages] != ["system", "user"]
                or any(not isinstance(item.get("content"), str) for item in messages)):
            raise ProviderError("model_invalid_input")
        try:
            prompt_size = serialized_prompt_bytes(messages)
            payload = {"model": self.model, "messages": messages, "stream": False,
                       "max_tokens": 4096,
                       "reasoning": {"enabled": False},
                       "provider": {"max_price": {"prompt": 0, "completion": 0}}}
            request_size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, AttributeError):
            raise ProviderError("model_invalid_input") from None
        if prompt_size > self.summary_prompt_budget_bytes or request_size > MAX_SUMMARY_REQUEST_BYTES:
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
            if choice["finish_reason"] != "stop":
                raise ValueError("Incomplete generation")
            content = choice["message"]["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SUMMARY_OUTPUT_BYTES:
                raise ValueError("Invalid content")
            return output_schema.model_validate_json(content).model_dump()
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise ProviderError("model_invalid_output") from None
