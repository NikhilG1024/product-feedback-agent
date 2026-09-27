"""Provider-neutral memory boundary; checkpoints must be durable and lease-fenced."""
from collections.abc import Callable
from typing import Protocol

from app.domain import MemoryContext, MemoryRecord

Checkpoint = Callable[[dict], None]


class ProviderError(RuntimeError):
    """Safe constant error code only; never include provider response bodies."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RetentionPending(ProviderError):
    def __init__(self):
        super().__init__('memory_pending')


class ReconciliationRequired(ProviderError):
    def __init__(self):
        super().__init__('memory_reconciliation_required')


class MemoryStore(Protocol):
    def ensure_retained(self, bank_id: str, record: MemoryRecord, *,
                        state: dict | None = None, checkpoint: Checkpoint | None = None) -> None: ...
    def recall(self, bank_id: str, query: str) -> MemoryContext: ...
