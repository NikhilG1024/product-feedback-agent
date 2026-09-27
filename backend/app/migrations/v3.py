"""Explicit, additive summary migration. Never invoked by application startup."""
import argparse
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from bson.int64 import Int64
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
    missing_indexes = {}
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
        missing_indexes[name] = []
        for spec in specs:
            for index in indexes:
                if (index["name"] == spec["name"] or
                        list(index["key"].items()) == spec["keys"]):
                    if not _index_matches(index, spec):
                        raise ValueError("Index options conflict: " + name + "/" + spec["name"])
            if not any(index["name"] == spec["name"] for index in indexes):
                missing_indexes[name].append(spec)
    result = {"version": 3, "dry_run": dry_run, "collections": list(contract.V3_SCHEMAS)}
    if dry_run:
        return result
    # Index builds on populated collections can grow far beyond a fixed allowance.
    # Deliberately over-reserve twice the logical collection size per missing index;
    # an unknown or near-limit size fails closed before any schema/index mutation.
    estimated_bytes = 16_384  # migration marker and collection metadata
    for name, specs in missing_indexes.items():
        if not specs:
            continue
        if name in existing:
            size = database.command("collStats", name)["size"]
            if type(size) not in (int, Int64) or size < 0:
                raise ValueError("Invalid collection size: " + name)
            estimated_bytes += max(65_536, size * 2) * len(specs)
        else:
            estimated_bytes += 65_536 * len(specs)
    capacity = CapacityGuard(database)
    capacity.check_write(estimated_bytes=estimated_bytes)
    for name, validator in contract.V3_SCHEMAS.items():
        if name not in existing:
            database.create_collection(name, validator=validator, validationLevel="strict", validationAction="error")
    for name, specs in contract.V3_INDEXES.items():
        for spec in specs:
            database[name].create_index(spec["keys"], **{key: value for key, value in spec.items() if key != "keys"})
    capacity.check_write()
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
