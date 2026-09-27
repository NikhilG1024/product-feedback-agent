"""v3 is additive and is exercised only against disposable test databases."""
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from pymongo import MongoClient


@pytest.fixture
def database():
    client = MongoClient(os.environ["TEST_MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=2000)
    db = client["test_summary_migration_" + uuid4().hex]
    yield db
    client.drop_database(db.name)
    client.close()


def _v2_with_record(database):
    from app.migrations.v2 import migrate
    migrate(database, False)
    database.products.insert_one({"_id": "P", "title": "Product", "provenance": {}})
    return database.products.find_one({})


def test_dry_run_makes_no_changes_and_repeat_apply_preserves_v2(database):
    from app.migrations.v3 import migrate
    original = _v2_with_record(database)
    before_collections = {item["name"]: item for item in database.list_collections()}
    before_indexes = {name: database[name].index_information() for name in before_collections}
    assert migrate(database, True)["dry_run"] is True
    assert {item["name"]: item for item in database.list_collections()} == before_collections
    migrate(database, False)
    migrate(database, False)
    assert database.products.find_one({}) == original
    assert all(database[name].index_information() == indexes for name, indexes in before_indexes.items()
               if name != "schema_migrations")
    assert database.schema_migrations.find_one({"_id": "v3"})["version"] == 3
    for name in ("product_summary_state", "product_summary_versions", "product_summary_inputs"):
        info = next(item for item in database.list_collections() if item["name"] == name)
        assert info["options"]["validationLevel"] == "strict"
        assert info["options"]["validationAction"] == "error"


def test_v3_unique_membership_and_version_keys(database):
    from app.migrations.v3 import migrate
    from pymongo.errors import DuplicateKeyError, WriteError
    migrate(database, False)
    stamp = datetime.now(timezone.utc)
    database.product_summary_inputs.insert_one({"_id": "a", "product_id": "P", "review_id": "r",
        "source": "user_submission", "admitted_at": stamp, "admission_sequence": 1,
        "incorporated_version": None})
    with pytest.raises(DuplicateKeyError):
        database.product_summary_inputs.insert_one({"_id": "b", "product_id": "P", "review_id": "r",
            "source": "user_submission", "admitted_at": stamp, "admission_sequence": 2,
            "incorporated_version": None})
    with pytest.raises(WriteError):
        database.product_summary_inputs.insert_one({"_id": 7, "product_id": "P", "review_id": "r2",
            "source": "user_submission", "admitted_at": stamp, "admission_sequence": 2,
            "incorporated_version": None})


def test_unknown_v3_validator_refused_before_any_mutation(database):
    from app.migrations.v3 import migrate
    database.create_collection("product_summary_state", validator={"foo": {"$exists": True}})
    with pytest.raises(ValueError, match="validator"):
        migrate(database, False)
    assert database.list_collection_names() == ["product_summary_state"]


def test_conflicting_index_options_refused_before_any_mutation(database):
    from app.migrations.v3 import migrate
    from app.migrations.v3 import contract
    database.create_collection("product_summary_versions",
        validator=contract.V3_SCHEMAS["product_summary_versions"],
        validationLevel="strict", validationAction="error")
    database.product_summary_versions.create_index([("product_id", 1), ("version", 1)],
        name="product_version", unique=False)
    with pytest.raises(ValueError, match="Index options conflict"):
        migrate(database, False)
    assert database.list_collection_names() == ["product_summary_versions"]
