"""Typed contracts shared by API, services, and repositories."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Principal(Contract):
    user_id: str
    role: Literal["reviewer", "pm"]


class ReviewInput(Contract):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=10000)
    rating: int = Field(strict=True, ge=1, le=5)
    asin: str | None = None

    @field_validator("title", "text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content must not be blank")
        return value


class Evidence(Contract):
    review_id: str
    quote: str


class FindingDraft(Contract):
    issue_type: str
    theme: str
    description: str
    evidence: list[Evidence]


class Scope(Contract):
    source: str
    sample_size: Literal[5] | None = None
    batch_id: str | None = None
    available_through: datetime | None = None
    evaluation: bool = False

    @field_validator("available_through")
    @classmethod
    def aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Timestamp must have a timezone")
        return value


class AnalysisInput(Contract):
    mode: str
    scope: Scope


class DecisionInput(Contract):
    kind: str
    rationale: str
    evidence_ids: list[str]
    available_through: datetime | None = None

    @field_validator("available_through")
    @classmethod
    def aware_datetime(cls, value: datetime | None) -> datetime | None:
        return Scope.aware_datetime(value)


class JobClaim(Contract):
    collection: str
    record_id: str
    owner_token: str
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must have a timezone")
        return value


class MemoryRecord(Contract):
    id: str
    content: str
    occurred_at: datetime
    metadata: dict[str, str | int | float | bool | None]

    @field_validator("occurred_at")
    @classmethod
    def aware_datetime(cls, value: datetime) -> datetime:
        return JobClaim.aware_datetime(value)


class MemoryContext(Contract):
    text: str
    record_ids: list[str]
