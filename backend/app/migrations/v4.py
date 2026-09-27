"""Explicit v4 upgrade for accepted initial-draft semantic decisions.

Only the exact previously deployed v3 validators are eligible. This command never
runs during application startup and never changes documents or indexes.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone

from pymongo import MongoClient

from app.migrations.v3 import contract


_NAMES = ("product_summary_state", "product_summary_versions")


def _prior_validator(current):
    prior = deepcopy(current)
    semantic = prior["$jsonSchema"]["properties"]["semantic_review"]
    semantic["properties"]["status"]["enum"].remove("accepted")
    semantic["oneOf"] = [branch for branch in semantic["oneOf"]
                         if branch["properties"]["status"]["enum"] != ["accepted"]]
    return prior


def migrate(database, dry_run: bool = True) -> dict:
    collections = {item["name"]: item for item in database.list_collections()}
    if database.schema_migrations.find_one({"_id": "v3"}) is None:
        raise ValueError("v3 migration must already be applied")
    upgrades = []
    for name in _NAMES:
        info = collections.get(name)
        if info is None or info.get("type") != "collection":
            raise ValueError("Missing v3 summary collection: " + name)
        options = info.get("options", {})
        current = contract.V3_SCHEMAS[name]
        old = _prior_validator(current)
        if options.get("validationLevel", "strict") != "strict" or options.get("validationAction", "error") != "error":
            raise ValueError("Unknown validator options: " + name)
        installed = options.get("validator")
        if installed not in (old, current):
            raise ValueError("Unknown validator: " + name)
        if database[name].count_documents({"$nor": [current]}, limit=1):
            raise ValueError("Existing records violate v4 validator: " + name)
        if installed == old:
            upgrades.append(name)
    result = {"version": 4, "dry_run": dry_run, "validator_upgrades": upgrades}
    if dry_run:
        return result
    for name in upgrades:
        database.command({"collMod": name, "validator": contract.V3_SCHEMAS[name],
                          "validationLevel": "strict", "validationAction": "error"})
    database.schema_migrations.update_one({"_id": "v4"}, {"$setOnInsert": {
        "version": 4, "applied_at": datetime.now(timezone.utc)}}, upsert=True)
    return result


def main():
    parser = argparse.ArgumentParser(description="Explicit accepted-draft validator upgrade")
    parser.add_argument("--apply", action="store_true", help="Apply after inspecting dry-run output")
    args = parser.parse_args()
    from app.config import Settings
    settings = Settings.from_env()
    with MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000) as client:
        print(migrate(client[settings.mongo_database], dry_run=not args.apply))


if __name__ == "__main__":
    main()
