"""PM-facing summary use cases. Reads never enqueue generation."""

import json

from pydantic import ValidationError

from app.errors import ServiceError
from app.integrations.llm import Answer
from app.integrations.memory import ProviderError
from app.summaries.contracts import RefreshInput, SummaryQuestionInput, SummarySettingsInput


class SummaryService:
    def __init__(self, database, repository, model=None, *, max_question_context_chars=6000):
        self.database = database
        self.repository = repository
        self.model = model
        self.max_question_context_chars = max_question_context_chars

    def _authorize(self, product_id, principal):
        if principal.role != "pm":
            raise ServiceError("forbidden", 403)
        if self.database.products.find_one({"_id": product_id}, {"_id": 1}) is None:
            raise ServiceError("product_not_found", 404)

    def get(self, product_id, principal):
        self._authorize(product_id, principal)
        return self.repository.current(product_id)

    def history(self, product_id, principal, cursor, limit):
        self._authorize(product_id, principal)
        try:
            return self.repository.history(product_id, cursor, limit)
        except ValueError:
            raise ServiceError("invalid_cursor", 422) from None

    def get_version(self, product_id, principal, version):
        self._authorize(product_id, principal)
        result = self.repository.version(product_id, version)
        if result is None:
            raise ServiceError("summary_version_not_found", 404)
        return result

    def settings(self, product_id, principal, payload: SummarySettingsInput):
        self._authorize(product_id, principal)
        self.repository.set_threshold(product_id, payload.update_threshold)
        return self.repository.current(product_id)

    def refresh(self, product_id, principal, key, payload: RefreshInput):
        self._authorize(product_id, principal)
        if not key or not key.strip() or len(key) > 200:
            raise ServiceError("invalid_idempotency_key", 422)
        try:
            self.repository.request_refresh(product_id, key, payload.reason)
        except ValueError:
            raise ServiceError("idempotency_conflict", 409) from None
        return self.repository.current(product_id)

    def question(self, product_id, principal, payload: SummaryQuestionInput):
        version = self.get_version(product_id, principal, payload.version)
        if not payload.question.strip():
            raise ServiceError("invalid_question", 422)
        evidence = list({(item.review_id, item.quote): item.model_dump()
                         for theme in version.themes for item in theme.evidence}.values())
        if not evidence:
            return {"answer": "Insufficient evidence in this summary version to answer the question.",
                    "evidence": [], "insufficient_evidence": True}
        context = [{"description": theme.description, "issue_type": theme.issue_type,
                    "polarity": theme.polarity, "evidence": [item.model_dump() for item in theme.evidence]}
                   for theme in version.themes]
        if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > self.max_question_context_chars:
            raise ServiceError("question_context_too_large", 422)
        if self.model is None:
            raise ServiceError("provider_not_configured", 503)
        try:
            result = Answer.model_validate(self.model.answer(payload.question, context, evidence))
        except ProviderError as exc:
            code = "model_rate_limited" if exc.code == "model_rate_limited" else "question_provider_failed"
            raise ServiceError(code, 429 if code == "model_rate_limited" else 503) from None
        except (ValidationError, TypeError, ValueError):
            raise ServiceError("invalid_answer_evidence", 502) from None
        allowed = {(item["review_id"], item["quote"]) for item in evidence}
        if (result.insufficient_evidence and result.evidence or
                not result.insufficient_evidence and not result.evidence or
                any((item.review_id, item.quote) not in allowed for item in result.evidence)):
            raise ServiceError("invalid_answer_evidence", 502)
        return result.model_dump()
