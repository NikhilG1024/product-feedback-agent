"""Accepted-draft validator upgrade against disposable Mongo."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError

from app.migrations import v3, v4


@pytest.fixture
def database():
    client = MongoClient(os.environ["TEST_MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=2000)
    db = client["test_summary_v4_" + uuid4().hex]
    v3.migrate(db, False)
    for name in v4._NAMES:
        db.command({"collMod": name, "validator": v4._prior_validator(v3.contract.V3_SCHEMAS[name]),
                    "validationLevel": "strict", "validationAction": "error"})
    yield db
    client.drop_database(db.name)
    client.close()


def _draft(status, *, reviewer=True):
    now = datetime.now(timezone.utc)
    review = {"status": status}
    if reviewer:
        review.update({"reviewer_id": "owner", "reviewer_type": "human", "reviewed_at": now,
                       "artifact_sha256": "a" * 64, "rubric_version": "explicit-draft-acceptance-v1"})
    return {"_id": "P:1", "product_id": "P", "version": 1, "parent_version": None,
            "job_id": "initial", "kind": "initial", "narrative": "Draft", "themes": [],
            "coverage": {"historical_sample_count": 0, "new_review_count": 0},
            "delta_review_ids": [], "model_identity": "test", "prompt_version": "v1",
            "guidance_references": [], "created_at": now, "semantic_review": review}


def test_v4_dry_run_then_upgrade_known_v3_validators(database):
    database.product_summary_versions.insert_one(_draft("pending", reviewer=False))
    before = list(database.product_summary_versions.find())
    assert v4.migrate(database, True)["validator_upgrades"] == list(v4._NAMES)
    with pytest.raises(WriteError):
        database.product_summary_versions.update_one({"_id": "P:1"}, {"$set": {
            "semantic_review": _draft("accepted")["semantic_review"]}})
    assert v4.migrate(database, False)["validator_upgrades"] == list(v4._NAMES)
    assert list(database.product_summary_versions.find()) == before
    assert v4.migrate(database, False)["validator_upgrades"] == []
    database.product_summary_versions.update_one({"_id": "P:1"}, {"$set": {
        "semantic_review": _draft("accepted")["semantic_review"]}})
    assert database.product_summary_versions.find_one({"_id": "P:1"})["semantic_review"]["status"] == "accepted"
    assert database.schema_migrations.find_one({"_id": "v4"})["version"] == 4


def test_v4_refuses_unknown_validator_without_mutating_other_collection(database):
    odd = deepcopy(v3.contract.V3_SCHEMAS["product_summary_versions"])
    odd["$jsonSchema"]["properties"]["narrative"]["maxLength"] = 3999
    database.command({"collMod": "product_summary_versions", "validator": odd})
    with pytest.raises(ValueError, match="Unknown validator"):
        v4.migrate(database, False)
    info = {item["name"]: item for item in database.list_collections()}
    assert info["product_summary_state"]["options"]["validator"] == v4._prior_validator(
        v3.contract.V3_SCHEMAS["product_summary_state"])
    assert database.schema_migrations.find_one({"_id": "v4"}) is None
