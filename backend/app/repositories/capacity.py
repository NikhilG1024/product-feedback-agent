"""Global logical quota preflight; concurrent external writes are not reserved."""
import math
from bson import BSON
from bson.int64 import Int64
from app.errors import ServiceError


class CapacityGuard:
    def __init__(self, database, *, capacity_bytes=400_000_000, reserve_bytes=1_000_000, enabled=True):
        self.database = database
        self.capacity_bytes, self.reserve_bytes, self.enabled = capacity_bytes, reserve_bytes, enabled

    def check_write(self, estimated_bytes=0):
        if not self.enabled: return
        try:
            client = self.database.client
            inventory = client.admin.command({'listDatabases': 1, 'nameOnly': False, 'authorizedDatabases': False})
            total = 0
            for item in inventory['databases']:
                name = item['name']
                if name in {'admin', 'local', 'config'}: continue
                stats = client[name].command('dbStats', scale=1)
                for field in ('dataSize', 'indexSize'):
                    value = stats[field]
                    if type(value) not in (int, float, Int64) or not math.isfinite(value) or value < 0:
                        raise ValueError('Invalid telemetry')
                    total += value
        except Exception:
            raise ServiceError('capacity_unavailable', 503) from None
        if total + self.reserve_bytes + estimated_bytes >= self.capacity_bytes:
            raise ServiceError('capacity_exceeded', 503)

    def check_documents(self, documents):
        # BSON size multiplier leaves headroom for indexes and document overhead.
        self.check_write(sum(len(BSON.encode(document)) for document in documents) * 4)

    def check_ready(self):
        self.database.command('ping')
        self.check_write()
        return True
