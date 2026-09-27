"""OpenAI-compatible llama-server summary client for the pinned local Qwen model."""

import json
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from app.integrations.http import JSONTransport, ProviderHTTPError
from app.integrations.memory import ProviderError
from app.integrations.openrouter_summary import (
    EVIDENCE_LIMIT_REMINDER, _compact_representative_evidence)
from app.summaries.generation import serialized_prompt_bytes

LOCAL_SUMMARY_MODEL = "qwen3-4b-instruct-2507-local"
LOCAL_SUMMARY_PROMPT_BYTES = 32000
LOCAL_SUMMARY_REQUEST_BYTES = 42000
LOCAL_SUMMARY_OUTPUT_BYTES = 100000
LOCAL_SUMMARY_MAX_TOKENS = 6144


def _review_quote_options(review: dict) -> list[str]:
    options = []
    for field in ("title", "text"):
        source = review.get(field)
        if not isinstance(source, str):
            raise ValueError("Invalid review source")
        source = source.strip()
        for value in (source if len(source) <= 500 else "", source[:250], source[-250:]):
            if value and value not in options:
                options.append(value)
    if not options:
        raise ValueError("Review has no quotable source")
    return options


def _batch_schema(output_schema: type[BaseModel], reviews: list[dict]) -> dict:
    """Grammar fixes one exact-source citation slot per fresh review."""
    schema = output_schema.model_json_schema()
    base_theme = schema["$defs"]["SummaryTheme"]
    themed = []
    for review in reviews:
        item = deepcopy(base_theme)
        item["properties"]["evidence"] = {
            "type": "array", "minItems": 1, "maxItems": 1,
            "items": {"type": "object", "additionalProperties": False,
                "properties": {"review_id": {"type": "string", "const": str(review["id"])},
                               "quote": {"type": "string", "enum": _review_quote_options(review)}},
                "required": ["review_id", "quote"]}}
        themed.append(item)
    schema["properties"]["themes"] = {"type": "array", "minItems": len(themed),
        "maxItems": len(themed), "prefixItems": themed,
        "items": themed[0] if len(themed) == 1 else {"anyOf": themed} if themed else {}}
    return schema


def _validate_batch_shape(data: dict, reviews: list[dict]) -> None:
    themes = data.get("themes") if isinstance(data, dict) else None
    if not isinstance(themes, list):
        raise ValueError("New-review themes are invalid")
    by_id = {str(review["id"]): review for review in reviews}
    seen = set()
    for theme in themes:
        evidence = theme.get("evidence") if isinstance(theme, dict) else None
        if not isinstance(evidence, list):
            raise ValueError("New-review evidence does not match constrained source")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("New-review evidence does not match constrained source")
            review_id = item.get("review_id")
            quote = item.get("quote")
            if (not isinstance(review_id, str) or review_id not in by_id or
                    not isinstance(quote, str) or not 1 <= len(quote) <= 500 or
                    not any(quote in by_id[review_id].get(field, "")
                            for field in ("title", "text"))):
                raise ValueError("New-review evidence does not match constrained source")
            seen.add(review_id)
    if seen != set(by_id):
        raise ValueError("New-review coverage does not match batch")


def validate_local_model_url(url: str) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid local summary endpoint") from None
    if (not url or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"):
        raise ValueError("Invalid local summary endpoint")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ngrok = bool(parsed.hostname and (parsed.hostname.endswith(".ngrok-free.app")
                  or parsed.hostname.endswith(".ngrok.app")
                  or parsed.hostname.endswith(".ngrok-free.dev")
                  or parsed.hostname.endswith(".ngrok.dev")))
    if not ((parsed.scheme == "http" and loopback and port is not None) or
            (parsed.scheme == "https" and ngrok)):
        raise ValueError("Local summary endpoint must be loopback or HTTPS ngrok")


class LocalSummaryModel:
    """A single configured local endpoint, with no provider fallback."""

    incremental_delta = True
    summary_max_reviews_per_call = 1

    summary_prompt_budget_bytes = (LOCAL_SUMMARY_PROMPT_BYTES -
                                   len(EVIDENCE_LIMIT_REMINDER.encode("utf-8")))

    def __init__(self, api_key: str, *, base_url: str,
                 model: str = LOCAL_SUMMARY_MODEL, timeout: float = 600,
                 transport: Any = None):
        validate_local_model_url(base_url)
        if model != LOCAL_SUMMARY_MODEL:
            raise ValueError("Only the pinned local Qwen summary model is allowed")
        self.model = model
        self.http = JSONTransport(base_url, api_key, timeout=timeout,
            max_request_bytes=LOCAL_SUMMARY_REQUEST_BYTES,
            max_response_bytes=LOCAL_SUMMARY_OUTPUT_BYTES + 4096, transport=transport)
        self.http.client.headers["ngrok-skip-browser-warning"] = "true"

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
            input_data = json.loads(messages[1]["content"])
            schema = _batch_schema(output_schema, input_data["new_reviews"])
            payload = {"model": self.model, "messages": bounded_messages,
                       "stream": False, "temperature": 0, "seed": 27,
                       "max_tokens": LOCAL_SUMMARY_MAX_TOKENS,
                       "response_format": {"type": "json_object", "schema": schema}}
            prompt_size = serialized_prompt_bytes(bounded_messages)
            request_size = len(json.dumps(payload, ensure_ascii=False,
                                          allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, AttributeError):
            raise ProviderError("model_invalid_input") from None
        if prompt_size > LOCAL_SUMMARY_PROMPT_BYTES or request_size > LOCAL_SUMMARY_REQUEST_BYTES:
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
            if not isinstance(content, str) or len(content.encode("utf-8")) > LOCAL_SUMMARY_OUTPUT_BYTES:
                raise ValueError("Invalid content")
            fresh_ids = {str(row["id"]) for row in input_data["new_reviews"]}
            parsed = _compact_representative_evidence(json.loads(content), fresh_ids)
            _validate_batch_shape(parsed, input_data["new_reviews"])
            return output_schema.model_validate(parsed).model_dump()
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, ValidationError):
            raise ProviderError("model_invalid_output") from None
