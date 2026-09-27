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
EVIDENCE_LIMIT_REMINDER = (
    "\nCritical schema limit: each theme's evidence array must contain 1 to 3 items, "
    "never 4 or more. One exact citation per new review somewhere in the summary "
    "is sufficient; do not repeat every review under every theme."
)


def _compact_representative_evidence(data: Any, fresh_ids: set[str]) -> Any:
    """Drop surplus representative citations, never a distinct fresh review."""
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        return data
    for theme in data["themes"]:
        if not isinstance(theme, dict) or not isinstance(theme.get("evidence"), list):
            continue
        evidence = theme["evidence"]
        if len(evidence) <= 3:
            continue
        if any(not isinstance(item, dict) or not isinstance(item.get("review_id"), str)
               or not isinstance(item.get("quote"), str) for item in evidence):
            continue
        fresh = []
        seen_fresh = set()
        for item in evidence:
            review_id = item["review_id"]
            if review_id in fresh_ids and review_id not in seen_fresh:
                fresh.append(item)
                seen_fresh.add(review_id)
        if len(fresh) > 3:
            continue  # Schema validation rejects a theme with four fresh reviews.
        selected = list(fresh)
        seen_pairs = {(item["review_id"], item["quote"]) for item in selected}
        for item in evidence:
            if len(selected) == 3:
                break
            pair = (item["review_id"], item["quote"])
            if item["review_id"] not in fresh_ids and pair not in seen_pairs:
                selected.append(item)
                seen_pairs.add(pair)
        theme["evidence"] = selected
    cited_fresh = {item.get("review_id") for theme in data["themes"]
                   if isinstance(theme, dict) and isinstance(theme.get("evidence"), list)
                   for item in theme["evidence"] if isinstance(item, dict)}
    if not fresh_ids <= cited_fresh:
        raise ValueError("Compaction would remove required fresh evidence")
    return data


class OpenRouterSummaryModel:
    """Pinned free endpoint; no alternate model or charged provider route."""

    summary_prompt_budget_bytes = (SUMMARY_PROMPT_BUDGET_BYTES -
                                   len(EVIDENCE_LIMIT_REMINDER.encode("utf-8")))

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
                or any(not isinstance(item, dict) for item in messages)
                or [item.get("role") for item in messages] != ["system", "user"]
                or any(not isinstance(item.get("content"), str) for item in messages)):
            raise ProviderError("model_invalid_input")
        try:
            bounded_messages = [{**messages[0], "content": messages[0]["content"] +
                                 EVIDENCE_LIMIT_REMINDER}, messages[1]]
            prompt_size = serialized_prompt_bytes(bounded_messages)
            payload = {"model": self.model, "messages": bounded_messages, "stream": False,
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
            fresh_ids = {str(row["id"]) for row in json.loads(messages[1]["content"])["new_reviews"]}
            parsed = _compact_representative_evidence(json.loads(content), fresh_ids)
            return output_schema.model_validate(parsed).model_dump()
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise ProviderError("model_invalid_output") from None
