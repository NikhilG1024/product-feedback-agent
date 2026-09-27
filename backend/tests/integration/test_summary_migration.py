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


def test_one_state_per_product_even_with_different_ids(database):
    from app.migrations.v3 import migrate
    from pymongo.errors import DuplicateKeyError
    migrate(database, False)
    database.product_summary_state.insert_one({"_id": "one", "product_id": "P"})
    with pytest.raises(DuplicateKeyError):
        database.product_summary_state.insert_one({"_id": "two", "product_id": "P"})


def test_version_requires_provenance_and_completed_semantic_review(database):
    from app.migrations.v3 import migrate
    from pymongo.errors import WriteError
    migrate(database, False)
    stamp = datetime.now(timezone.utc)
    minimal = {"_id": "v1", "product_id": "P", "version": 1, "job_id": "j",
        "kind": "initial", "narrative": "Supported", "themes": [],
        "coverage": {"historical_sample_count": 1, "new_review_count": 0},
        "created_at": stamp}
    with pytest.raises(WriteError):
        database.product_summary_versions.insert_one(minimal)
    complete = {**minimal, "parent_version": None, "delta_review_ids": [],
        "model_identity": "local", "prompt_version": "v1", "guidance_references": [],
        "semantic_review": {"status": "pending"}}
    database.product_summary_versions.insert_one(complete)
    with pytest.raises(WriteError):
        database.product_summary_versions.insert_one({**complete, "_id": "v2", "version": 2,
            "job_id": "j2", "semantic_review": {"status": "approved"}})


def test_populated_index_build_refuses_insufficient_headroom_before_mutation(database, monkeypatch):
    from app.migrations import v3
    from app.repositories.capacity import CapacityGuard
    from app.errors import ServiceError
    database.create_collection("product_summary_state",
        validator=v3.contract.V3_SCHEMAS["product_summary_state"],
        validationLevel="strict", validationAction="error")
    database.product_summary_state.insert_one({"_id": "P", "product_id": "P", "padding": "x" * 1_000_000})
    inventory = database.client.admin.command({"listDatabases": 1, "nameOnly": False,
        "authorizedDatabases": False})
    total = sum(database.client[item["name"]].command("dbStats", scale=1)[field]
                for item in inventory["databases"] if item["name"] not in {"admin", "local", "config"}
                for field in ("dataSize", "indexSize"))
    monkeypatch.setattr(v3, "CapacityGuard", lambda db: CapacityGuard(db,
        capacity_bytes=total + 1_000_000 + 200_000))
    with pytest.raises(ServiceError, match="capacity_exceeded"):
        v3.migrate(database, False)
    assert database.list_collection_names() == ["product_summary_state"]
    assert set(database.product_summary_state.index_information()) == {"_id_"}


def test_populated_index_preflight_accepts_bson_int64_size(database, monkeypatch):
    from bson.int64 import Int64
    from app.migrations import v3
    database.create_collection("product_summary_state",
        validator=v3.contract.V3_SCHEMAS["product_summary_state"],
        validationLevel="strict", validationAction="error")
    database.product_summary_state.insert_one({"_id": "P", "product_id": "P"})
    original_command = database.command

    def command(name, *args, **kwargs):
        result = original_command(name, *args, **kwargs)
        if name == "collStats":
            return {**result, "size": Int64(result["size"])}
        return result

    monkeypatch.setattr(database, "command", command)
    assert v3.migrate(database, False)["version"] == 3
    assert database.product_summary_state.index_information()["one_state_per_product"]["unique"] is True
