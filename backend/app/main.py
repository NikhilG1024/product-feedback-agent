"""FastAPI application shell and safe operational endpoints."""

from collections.abc import Callable
from contextlib import asynccontextmanager, ExitStack

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from app.config import Settings
from app.errors import ServiceError


class HealthResponse(BaseModel):
    status: str


class ErrorBody(BaseModel):
    code: str


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_response(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=ErrorResponse(error=ErrorBody(code=code)).model_dump())


class BodyLimitMiddleware:
    def __init__(self, app: Callable, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                await error_response("request_too_large", 413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings, services: object | None = None, *, lifespan=None) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.services = services
    app.add_middleware(BodyLimitMiddleware, limit=settings.http_body_limit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        code = exc.code
        secrets = (settings.reviewer_token, settings.pm_token, settings.llm_api_key,
                   settings.openrouter_api_key, settings.hindsight_api_key, settings.mongo_uri)
        if any(secret and secret in code for secret in secrets):
            code = "internal_error"
        if code == 'legacy_analysis_unsupported':
            return JSONResponse(status_code=409, content={'error': {'code': code, 'detail':
                'Create a new analysis run for the product and batch; legacy results have no v2 evidence snapshot.'}})
        return error_response(code, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response("invalid_request", 422)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response("http_error", exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return error_response("internal_error", 500)

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    def ready() -> HealthResponse | JSONResponse:
        checker = getattr(services, "check_ready", None)
        try:
            if checker is not None and checker():
                return HealthResponse(status="ok")
        except Exception:
            pass
        return JSONResponse(status_code=503, content=HealthResponse(status="unavailable").model_dump())

    from app.api.products import router as products_router
    from app.api.reviews import router as reviews_router
    app.include_router(products_router)
    app.include_router(reviews_router)
    from app.api.analysis import router as analysis_router
    app.include_router(analysis_router)
    from app.api.decisions import router as decisions_router
    app.include_router(decisions_router)
    from app.api.questions import router as questions_router
    app.include_router(questions_router)
    from app.summaries.progress_api import router as summary_progress_router
    app.include_router(summary_progress_router)
    from app.summaries.api import router as summary_router
    app.include_router(summary_router)
    from app.api.events import router as events_router
    app.include_router(events_router)
    return app


def configured_app() -> FastAPI:
    from types import SimpleNamespace
    from app.repositories.mongo import connect
    from app.services.reviews import ReviewService
    settings = Settings.from_env()
    client, database = connect(settings)
    from app.repositories.capacity import CapacityGuard
    capacity = CapacityGuard(database, capacity_bytes=settings.database_capacity_bytes,
                             reserve_bytes=settings.capacity_write_reserve_bytes, enabled=settings.capacity_checks_enabled)
    from app.services.analysis import AnalysisService
    from app.integrations.llm import DeepSeekModel
    from app.integrations.hindsight import HindsightMemory
    from app.repositories.jobs import JobRepository
    from app.worker import Worker
    model = DeepSeekModel(settings.llm_api_key, base_url=settings.llm_api_url,
                          model=settings.llm_model, timeout=settings.provider_timeout_seconds) if settings.llm_api_key else None
    memory = HindsightMemory(settings.hindsight_api_url, settings.hindsight_api_key,
                             timeout=settings.provider_timeout_seconds) if settings.hindsight_api_url and settings.hindsight_api_key else None
    if settings.summary_llm_provider == "local":
        from app.integrations.local_summary import LocalSummaryModel
        summary_model = LocalSummaryModel(settings.local_model_api_key,
            base_url=settings.local_model_api_url, model=settings.local_model_name,
            timeout=settings.summary_provider_timeout_seconds) if (
                settings.local_model_api_url and settings.local_model_api_key) else None
    else:
        from app.integrations.groq_summary import GroqSummaryModel
        summary_model = GroqSummaryModel(settings.groq_api_key,
            base_url=settings.llm_api_url, model=settings.llm_model,
            timeout=settings.summary_provider_timeout_seconds) if settings.groq_api_key else None
    analysis = AnalysisService(database, model, memory, max_reviews=settings.max_analysis_reviews,
                               max_chunk_reviews=settings.max_chunk_reviews, max_chunk_chars=settings.max_chunk_chars,
                               max_findings=settings.max_analysis_findings, capacity=capacity)
    from app.services.decisions import DecisionService
    from app.services.review_processing import ReviewProcessor
    from app.summaries.repository import SummaryRepository
    summary_repository = SummaryRepository(database, capacity=capacity)
    decisions = DecisionService(database, memory, capacity=capacity, summaries=summary_repository)
    review_processor = ReviewProcessor(database, model, memory, capacity=capacity)
    worker = Worker(JobRepository(database, settings.max_job_attempts, capacity=capacity), {'analysis_runs': analysis.handle, 'reviews': review_processor.handle, 'decisions': decisions.handle},
                    lease_seconds=settings.job_lease_seconds)
    from app.services.questions import QuestionService
    questions = QuestionService(database, model, max_question_chars=settings.max_question_chars,
                                max_context_chars=settings.max_question_context_chars)
    from app.summaries.service import SummaryService
    from app.summaries.worker import SummaryWorker
    summaries = SummaryService(database, summary_repository, model,
                               max_question_context_chars=settings.max_question_context_chars)
    summary_worker = SummaryWorker(database, summary_repository, summary_model, memory=memory,
                                   lease_seconds=settings.job_lease_seconds)
    services = SimpleNamespace(questions=questions, summaries=summaries,
                               reviews=ReviewService(database, submission_limit=settings.review_submission_limit,
                                                     capacity=capacity, summaries=summary_repository),
                               analysis=analysis, decisions=decisions, worker=worker,
                               check_ready=capacity.check_ready)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            # ExitStack attempts every cleanup even if another closer raises.
            with ExitStack() as cleanup:
                cleanup.callback(client.close)
                if memory is not None: cleanup.callback(memory.close)
                if model is not None: cleanup.callback(model.close)
                if summary_model is not None: cleanup.callback(summary_model.close)
                cleanup.callback(worker.stop)
                cleanup.callback(summary_worker.stop)

    app = create_app(settings, services, lifespan=lifespan)
    app.state.mongo_client = client
    app.state.worker = worker
    app.state.summary_worker = summary_worker
    return app
