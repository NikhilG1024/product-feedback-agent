"""Explicit, additive summary migration. Never invoked by application startup."""
import argparse
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

from app.repositories.capacity import CapacityGuard


_contract_path = Path(__file__).resolve().parents[3] / "data/scripts/mongo_contract.py"
_spec = importlib.util.spec_from_file_location("feedback_mongo_contract_v3", _contract_path)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)


def _index_matches(existing, requested):
    return (list(existing["key"].items()) == requested["keys"] and
            all(existing.get(key) == requested.get(key)
                for key in ("unique", "partialFilterExpression", "expireAfterSeconds")))


def migrate(database, dry_run: bool) -> dict:
    existing = {item["name"]: item for item in database.list_collections()}
    for name, validator in contract.V3_SCHEMAS.items():
        if name not in existing:
            continue
        info = existing[name]
        options = info.get("options", {})
        if (info.get("type") != "collection" or options.get("validator") != validator or
                options.get("validationAction", "error") != "error" or
                options.get("validationLevel", "strict") != "strict"):
            raise ValueError("Unknown validator: " + name)
        if database[name].count_documents({"$nor": [validator]}, limit=1):
            raise ValueError("Existing records violate validator: " + name)
    # Validate all existing names and equivalent keys before creating anything.
    for name, specs in contract.V3_INDEXES.items():
        indexes = list(database[name].list_indexes()) if name in existing else []
        for spec in specs:
            for index in indexes:
                if (index["name"] == spec["name"] or
                        list(index["key"].items()) == spec["keys"]):
                    if not _index_matches(index, spec):
                        raise ValueError("Index options conflict: " + name + "/" + spec["name"])
    result = {"version": 3, "dry_run": dry_run, "collections": list(contract.V3_SCHEMAS)}
    if dry_run:
        return result
    CapacityGuard(database).check_write(estimated_bytes=100_000)
    for name, validator in contract.V3_SCHEMAS.items():
        if name not in existing:
            database.create_collection(name, validator=validator, validationLevel="strict", validationAction="error")
    for name, specs in contract.V3_INDEXES.items():
        for spec in specs:
            database[name].create_index(spec["keys"], **{key: value for key, value in spec.items() if key != "keys"})
    database.schema_migrations.update_one({"_id": "v3"},
        {"$setOnInsert": {"version": 3, "applied_at": datetime.now(timezone.utc)}}, upsert=True)
    return result


def main():
    parser = argparse.ArgumentParser(description="Additive v3 summary migration")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.config import Settings
    settings = Settings.from_env()
    with MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000) as client:
        print(migrate(client[settings.mongo_database], args.dry_run))


if __name__ == "__main__":
    main()
