"""Explicit additive migration; never run implicitly on application startup."""
import argparse
import importlib.util
from pathlib import Path
from datetime import datetime, timezone
from pymongo import MongoClient

_contract_path = Path(__file__).resolve().parents[3] / 'data/scripts/mongo_contract.py'
_spec = importlib.util.spec_from_file_location('feedback_mongo_contract', _contract_path)
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)


def migrate(database, dry_run: bool) -> dict:
    existing = {c['name']: c for c in database.list_collections()}
    for name, validator in contract.V2_SCHEMAS.items():
        if name not in existing:
            continue
        info = existing[name]
        options = info.get('options', {})
        accepted = [validator]
        if name in contract.SCHEMAS:
            accepted.append(contract.SCHEMAS[name])
        if (info.get('type') != 'collection' or options.get('validator') not in accepted
                or options.get('validationAction', 'error') != 'error'
                or options.get('validationLevel', 'strict') != 'strict'):
            raise ValueError('Unknown validator: ' + name)
        if database[name].count_documents({'$nor': [validator]}, limit=1):
            raise ValueError('Existing records violate proposed validator: ' + name)
    # Inspect all names/options before any mutation, allowing safe restart after partial progress.
    for name, specs in contract.V2_INDEXES.items():
        indexes = list(database[name].list_indexes()) if name in existing else []
        for spec in specs:
            for index in indexes:
                if index['name'] == spec['name'] and (list(index['key'].items()) != spec['keys'] or any(index.get(k) != spec.get(k) for k in ('unique','partialFilterExpression','expireAfterSeconds'))):
                    raise ValueError('Index options conflict: ' + name + '/' + spec['name'])
    result = {'version': 2, 'dry_run': dry_run, 'collections': list(contract.V2_SCHEMAS)}
    if dry_run:
        return result
    for name, validator in contract.V2_SCHEMAS.items():
        if name in existing:
            database.command('collMod', name, validator=validator, validationLevel='strict', validationAction='error')
        else:
            database.create_collection(name, validator=validator, validationLevel='strict', validationAction='error')
    for name, specs in contract.V2_INDEXES.items():
        for spec in specs:
            database[name].create_index(spec['keys'], **{k:v for k,v in spec.items() if k != 'keys'})
    database.schema_migrations.update_one({'_id':'v2'}, {'$setOnInsert':{'version':2,'applied_at':datetime.now(timezone.utc)}}, upsert=True)
    return result


def main():
    parser=argparse.ArgumentParser(description='Additive v2 migration; database ceiling remains 400,000,000 bytes.')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    from app.config import Settings
    settings=Settings.from_env()
    with MongoClient(settings.mongo_uri,tz_aware=True,serverSelectionTimeoutMS=5000) as client:
        try:
            print(migrate(client[settings.mongo_database],args.dry_run))
        except Exception:
            raise SystemExit('Migration failed; verify validators, indexes and connectivity. No collection was dropped.') from None

if __name__=='__main__': main()
