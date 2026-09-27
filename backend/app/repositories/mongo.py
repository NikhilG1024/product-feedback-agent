"""Explicit database construction with bounded connection waits."""
from pymongo import MongoClient


def connect(settings):
    client = MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    return client, client[settings.mongo_database]
