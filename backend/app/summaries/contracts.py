"""Public summary contracts and separately validated model output."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryEvidence(Contract):
    review_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=500)


class SummaryTheme(Contract):
    id: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    issue_type: Literal["reported_defect", "preference", "feature_request", "other"]
    polarity: Literal["positive", "negative", "mixed", "neutral"]
    evidence: list[SummaryEvidence] = Field(min_length=1, max_length=3)


class GeneratedSummary(Contract):
    """Only content a model may provide; provenance and counts belong to the app."""
    narrative: str = Field(min_length=1, max_length=4000)
    themes: list[SummaryTheme] = Field(max_length=30)
    contradictions: list[str] = Field(default_factory=list, max_length=10)


class CompactGeneratedSummary(GeneratedSummary):
    """Bound new narratives without invalidating immutable historical versions."""
    narrative: str = Field(min_length=1, max_length=900)


class SummaryCoverage(Contract):
    historical_sample_count: int = Field(strict=True, ge=0)
    new_review_count: int = Field(strict=True, ge=0)


class SemanticReview(Contract):
    status: Literal["pending", "approved", "rejected"] = "pending"
    reviewer_id: str | None = None
    reviewer_type: Literal["human", "automated"] | None = None
    reviewed_at: datetime | None = None
    artifact_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    rubric_version: str | None = None
    factual_support: bool | None = None
    coverage: bool | None = None
    classification: bool | None = None

    @model_validator(mode="after")
    def completed_review_is_attributed(self):
        if self.status != "pending" and any(not getattr(self, name) for name in
            ("reviewer_id", "reviewer_type", "reviewed_at", "artifact_sha256",
             "rubric_version")):
            raise ValueError("Completed semantic review requires attribution")
        if self.status == "approved" and any(getattr(self, name) is not True for name in
            ("factual_support", "coverage", "classification")):
            raise ValueError("Approved semantic review requires all rubric gates to pass")
        return self


class SummaryVersion(GeneratedSummary):
    product_id: str = Field(min_length=1)
    version: int = Field(strict=True, ge=1)
    parent_version: int | None = Field(default=None, strict=True, ge=1)
    job_id: str = Field(min_length=1)
    kind: Literal["initial", "reviews", "guidance"]
    coverage: SummaryCoverage
    delta_review_ids: list[str] = Field(max_length=20)
    manifest_ref: str | None = None
    model_identity: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    guidance_references: list[str]
    created_at: datetime
    published_at: datetime | None = None
    semantic_review: SemanticReview = Field(default_factory=SemanticReview)


class SummaryView(Contract):
    product_id: str = Field(min_length=1)
    current: SummaryVersion | None
    initial_candidate: SummaryVersion | None = None
    last_updated_at: datetime | None
    update_threshold: int = Field(strict=True, ge=1, le=100)
    pending_review_count: int = Field(strict=True, ge=0)
    status: Literal["uninitialized", "waiting", "queued", "updating", "ready", "failed"]
    error_code: str | None = None
    memory_status: str

    @model_validator(mode="after")
    def timestamp_tracks_publication(self):
        if (self.current is None) != (self.last_updated_at is None):
            raise ValueError("Current summary and last update must appear together")
        return self


class HistoryPage(Contract):
    items: list[SummaryVersion]
    next_cursor: str | None


class SummarySettingsInput(Contract):
    update_threshold: int = Field(strict=True, ge=1, le=100)


class RefreshInput(Contract):
    reason: Literal["pending_reviews", "guidance"]


class SummaryQuestionInput(Contract):
    version: int = Field(strict=True, ge=1)
    question: str = Field(min_length=1, max_length=1000)
