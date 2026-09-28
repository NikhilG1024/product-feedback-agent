"""Validated server configuration. Secret values are never included in errors."""

from dataclasses import dataclass, fields
import os
from urllib.parse import urlsplit


FREE_LLM_URL = "https://api.groq.com/openai/v1"
FREE_LLM_MODEL = "openai/gpt-oss-20b"
OPENROUTER_SUMMARY_URL = "https://openrouter.ai/api/v1"
OPENROUTER_SUMMARY_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def validate_free_llm(base_url: str, model: str) -> None:
    # Endpoint/model guard prevents fallback; account billing must remain on Groq Free.
    if base_url.rstrip('/') != FREE_LLM_URL or model != FREE_LLM_MODEL:
        raise ValueError("Only the approved free-plan Groq endpoint and model are allowed")


@dataclass(frozen=True, repr=False)
class Settings:
    mongo_uri: str
    mongo_database: str
    reviewer_token: str
    pm_token: str
    hindsight_api_url: str = ""
    hindsight_api_key: str = ""
    llm_api_url: str = FREE_LLM_URL
    llm_api_key: str = ""
    llm_model: str = FREE_LLM_MODEL
    groq_api_key: str = ""
    summary_llm_provider: str = "local"
    local_model_api_url: str = ""
    local_model_api_key: str = ""
    local_model_name: str = "qwen3-1.7b-local"
    openrouter_api_key: str = ""
    openrouter_api_url: str = OPENROUTER_SUMMARY_URL
    openrouter_summary_model: str = OPENROUTER_SUMMARY_MODEL
    cors_origins: tuple[str, ...] = ()
    reviewer_user_id: str = "demo-reviewer"
    pm_user_id: str = "demo-pm"
    http_body_limit: int = 65536
    page_size: int = 20
    max_page_size: int = 100
    max_analysis_reviews: int = 1500
    max_analysis_findings: int = 100
    max_chunk_reviews: int = 5
    max_chunk_chars: int = 3000
    provider_timeout_seconds: int = 60
    summary_provider_timeout_seconds: int = 600
    job_lease_seconds: int = 180
    max_job_attempts: int = 5
    database_capacity_bytes: int = 400_000_000
    capacity_write_reserve_bytes: int = 1_000_000
    capacity_checks_enabled: bool = True
    review_submission_limit: int = 10
    max_question_chars: int = 2000
    max_question_context_chars: int = 2000
    summary_initialization_progress_path: str = ""
    summary_initialization_stale_seconds: int = 120
    public_demo_enabled: bool = False

    def __repr__(self) -> str:
        return "Settings(<redacted>)"

    def __post_init__(self) -> None:
        validate_free_llm(self.llm_api_url, self.llm_model)
        if self.summary_llm_provider not in {"local", "groq"}:
            raise ValueError('Unsupported summary provider')
        if self.local_model_api_url:
            from app.integrations.local_summary import SUPPORTED_LOCAL_MODELS, validate_local_model_url
            validate_local_model_url(self.local_model_api_url)
            if self.local_model_name not in SUPPORTED_LOCAL_MODELS:
                raise ValueError('Only the pinned local Qwen summary model is allowed')
        if (self.openrouter_api_url.rstrip('/') != OPENROUTER_SUMMARY_URL or
                self.openrouter_summary_model != OPENROUTER_SUMMARY_MODEL):
            raise ValueError('Only the approved free OpenRouter summary model is allowed')
        for field in fields(self):
            if field.type is int and (type(getattr(self, field.name)) is not int or getattr(self, field.name) < 1):
                raise ValueError('Invalid numeric limit')
        if not 1 <= self.max_job_attempts <= 100 or self.max_question_chars > 2000:
            raise ValueError('Invalid operational limit')
        if self.database_capacity_bytes > 400_000_000 or self.capacity_write_reserve_bytes >= self.database_capacity_bytes:
            raise ValueError('Invalid capacity limit')
        if type(self.capacity_checks_enabled) is not bool:
            raise ValueError('Invalid capacity flag')
        if type(self.public_demo_enabled) is not bool:
            raise ValueError('Invalid public demo flag')
        if not self.capacity_checks_enabled:
            parsed_mongo = urlsplit(self.mongo_uri)
            if parsed_mongo.hostname not in {'localhost', '127.0.0.1', '::1'} or not self.mongo_database.startswith('test_'):
                raise ValueError('Capacity bypass requires a local test database')
        if not 1 <= self.max_analysis_findings <= 1000:
            raise ValueError('Invalid finding limit')
        if not self.mongo_uri or not self.mongo_database:
            raise ValueError("MongoDB configuration is required")
        if not self.reviewer_token or not self.pm_token:
            raise ValueError("Both demo bearer tokens are required")
        if self.reviewer_token == self.pm_token:
            raise ValueError("Demo bearer tokens must be distinct")
        if not self.reviewer_user_id or not self.pm_user_id or self.reviewer_user_id == self.pm_user_id:
            raise ValueError("Demo identity IDs must be distinct")
        if self.http_body_limit < 1 or self.page_size < 1 or self.max_page_size < self.page_size:
            raise ValueError("Invalid request or pagination limit")
        for origin in self.cors_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.username is not None or parsed.query or parsed.fragment:
                raise ValueError("CORS origins must be absolute HTTP(S) origins")

    @classmethod
    def from_env(cls) -> "Settings":
        origins = tuple(origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip())
        limits = {}
        for field in fields(cls):
            if field.type is int:
                try:
                    limits[field.name] = int(os.getenv(field.name.upper(), str(field.default)))
                except ValueError:
                    raise ValueError('Invalid numeric configuration') from None
        capacity_flag = os.getenv('CAPACITY_CHECKS_ENABLED', 'true').lower()
        if capacity_flag not in {'true', 'false'}: raise ValueError('Invalid capacity flag')
        public_demo_flag = os.getenv('PUBLIC_DEMO_ENABLED', 'false').lower()
        if public_demo_flag not in {'true', 'false'}: raise ValueError('Invalid public demo flag')
        return cls(
            **limits,
            capacity_checks_enabled=capacity_flag == 'true',
            public_demo_enabled=public_demo_flag == 'true',
            mongo_uri=os.getenv("MONGODB_URI", ""),
            mongo_database=os.getenv("MONGODB_DATABASE", ""),
            reviewer_token=os.getenv("DEMO_REVIEWER_TOKEN", ""),
            pm_token=os.getenv("DEMO_PM_TOKEN", ""),
            hindsight_api_url=os.getenv("HINDSIGHT_API_URL", ""),
            hindsight_api_key=os.getenv("HINDSIGHT_API_KEY", ""),
            llm_api_url=os.getenv("LLM_API_URL") or FREE_LLM_URL,
            llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL") or FREE_LLM_MODEL,
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            summary_llm_provider=os.getenv("SUMMARY_LLM_PROVIDER") or "local",
            local_model_api_url=os.getenv("LOCAL_MODEL_API_URL", ""),
            local_model_api_key=os.getenv("LOCAL_MODEL_API_KEY", ""),
            local_model_name=os.getenv("LOCAL_MODEL_NAME") or "qwen3-1.7b-local",
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_api_url=os.getenv("OPENROUTER_SUMMARY_URL") or OPENROUTER_SUMMARY_URL,
            openrouter_summary_model=os.getenv("OPENROUTER_SUMMARY_MODEL") or OPENROUTER_SUMMARY_MODEL,
            cors_origins=origins,
            reviewer_user_id=os.getenv("DEMO_REVIEWER_USER_ID", "demo-reviewer"),
            pm_user_id=os.getenv("DEMO_PM_USER_ID", "demo-pm"),
            summary_initialization_progress_path=os.getenv("SUMMARY_INITIALIZATION_PROGRESS_PATH", ""),
        )
