import pytest

from app.config import Settings


@pytest.fixture
def settings():
    return Settings(
        mongo_uri="mongodb://localhost:27017",
        mongo_database="product_feedback_test",
        reviewer_token="reviewer-secret-value",
        pm_token="pm-secret-value",
        cors_origins=("https://app.example.test",),
    )
