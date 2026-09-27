from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.summaries.contracts import (
    GeneratedSummary, RefreshInput, SummarySettingsInput, SummaryVersion, SummaryView,
)


def test_threshold_is_a_strict_bounded_integer():
    for value in (1, 100):
        assert SummarySettingsInput(update_threshold=value).update_threshold == value
    for value in (0, 101, True, 1.0, "1"):
        with pytest.raises(ValidationError):
            SummarySettingsInput(update_threshold=value)


def test_summary_view_requires_publication_timestamp_exactly_with_current():
    base = dict(product_id="P", update_threshold=1, pending_review_count=0,
                status="uninitialized", error_code=None, memory_status="idle")
    assert SummaryView(**base, current=None, last_updated_at=None).current is None
    with pytest.raises(ValidationError):
        SummaryView(**base, current=None, last_updated_at=datetime.now(timezone.utc))
    with pytest.raises(ValidationError):
        SummaryView(**base, current=None, last_updated_at=None, surprise=True)


def test_generated_output_rejects_excess_instead_of_slicing():
    evidence = [{"review_id": "r", "quote": "exact"}]
    theme = {"id": "hinge", "description": "Breakage", "issue_type": "reported_defect",
             "polarity": "negative", "evidence": evidence}
    assert GeneratedSummary(narrative="Supported", themes=[theme]).themes[0].evidence[0].quote == "exact"
    for value in ({"narrative": "x" * 4001, "themes": []},
                  {"narrative": "ok", "themes": [theme] * 31},
                  {"narrative": "ok", "themes": [{**theme, "evidence": evidence * 4}]},
                  {"narrative": "ok", "themes": [{**theme, "evidence": [{"review_id": "r", "quote": "x" * 501}]}]}):
        with pytest.raises(ValidationError):
            GeneratedSummary(**value)


def test_version_and_refresh_contracts_are_strict():
    now = datetime.now(timezone.utc)
    version = SummaryVersion(product_id="P", version=1, parent_version=None, job_id="j",
        kind="initial", narrative="Supported", themes=[],
        coverage={"historical_sample_count": 20, "new_review_count": 0},
        delta_review_ids=[], manifest_ref="m", model_identity="local:model",
        prompt_version="v1", guidance_references=[], created_at=now, published_at=now)
    assert version.version == 1
    with pytest.raises(ValidationError):
        SummaryVersion(**{**version.model_dump(), "prevalence": 0.5})
    assert RefreshInput(reason="guidance").reason == "guidance"
    with pytest.raises(ValidationError):
        RefreshInput(reason="anything")
