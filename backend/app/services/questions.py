"""Questions over a completed run's published evidence, never generated queries."""
import json
from pydantic import ValidationError
from app.errors import ServiceError
from app.integrations.llm import Answer
from app.integrations.memory import ProviderError
from app.repositories.analysis import AnalysisRepository


class QuestionService:
    def __init__(self, database, model, *, max_question_chars=2000, max_context_chars=2000):
        self.repository = AnalysisRepository(database)
        self.model = model
        self.max_question_chars, self.max_context_chars = max_question_chars, max_context_chars

    def answer(self, product_id: str, run_id: str, question: str) -> dict:
        if not question.strip() or len(question) > self.max_question_chars:
            raise ServiceError('invalid_question', 422)
        run = self.repository.get(run_id)
        if run is None or run['parent_asin'] != product_id: raise ServiceError('run_not_found', 404)
        self.repository.require_readable(run)
        if run['status'] != 'completed': raise ServiceError('run_not_completed', 409)
        findings = self.repository.findings(run)
        fields = ('issue_type', 'theme', 'description', 'supporting_review_count', 'evidence', 'semantic_support')
        findings = [{key: row[key] for key in fields} for row in findings]
        evidence = list({(item['review_id'], item['quote']): item for row in findings for item in row['evidence']}.values())
        if len(json.dumps({'findings':findings, 'evidence':evidence}, ensure_ascii=False)) > self.max_context_chars:
            raise ServiceError('question_context_too_large', 422)
        if not evidence:
            return {'answer':'Insufficient evidence in this analysis run to answer the question.',
                    'evidence':[], 'insufficient_evidence':True}
        if self.model is None: raise ServiceError('provider_not_configured', 503)
        try:
            result = Answer.model_validate(self.model.answer(question, findings, evidence))
        except ProviderError as exc:
            if exc.code == 'model_rate_limited':
                raise ServiceError('model_rate_limited', 429) from None
            raise ServiceError('question_provider_failed', 503) from None
        except (ValidationError, TypeError, ValueError):
            raise ServiceError('invalid_answer_evidence', 502) from None
        allowed = {(item['review_id'], item['quote']) for item in evidence}
        if (result.insufficient_evidence and result.evidence
                or not result.insufficient_evidence and not result.evidence
                or any((item.review_id, item.quote) not in allowed for item in result.evidence)):
            raise ServiceError('invalid_answer_evidence', 502)
        return result.model_dump()
