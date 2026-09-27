"""Synchronous durable worker. Provider handlers must explicitly confirm completion.

Handlers receive a record and context, checkpoint accepted provider operation IDs,
then return JobResult(completed=False) until the provider confirms completion.
Handlers must use bounded provider timeouts. stop() prevents publication and leaves
unfinished records recoverable when their lease expires.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Thread


def utcnow():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JobResult:
    completed: bool = False
    updates: dict = field(default_factory=dict)
    delay_seconds: int = 5


@dataclass
class JobContext:
    repository: object
    claim: object
    clock: object
    cancelled: Event
    shutdown: Event

    def checkpoint(self, stage, value):
        return not (self.cancelled.is_set() or self.shutdown.is_set()) and self.repository.checkpoint(
            self.claim, stage, value, self.clock())


class Worker:
    def __init__(self, repository, handlers, *, clock=utcnow, lease_seconds=180,
                 renewal_interval=None):
        self.repository = repository
        self.handlers = dict(handlers)
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.renewal_interval = renewal_interval or lease_seconds / 3
        if not 0 < self.renewal_interval < lease_seconds:
            raise ValueError('Renewal interval must be shorter than lease')
        self.stopped = Event()
        self.next_queue = 0

    def stop(self):
        self.stopped.set()

    def tick(self):
        if self.stopped.is_set():
            return False
        queues = list(self.handlers.items())
        for offset in range(len(queues)):
            index = (self.next_queue + offset) % len(queues)
            collection, handler = queues[index]
            claim = self.repository.claim(collection, self.clock(), self.lease_seconds)
            if claim is None:
                continue
            self.next_queue = (index + 1) % len(queues)
            cancelled, done = Event(), Event()
            context = JobContext(self.repository, claim, self.clock, cancelled, self.stopped)

            def renew_lease():
                while not done.wait(self.renewal_interval):
                    if self.stopped.is_set():
                        cancelled.set()
                        return
                    try:
                        if self.repository.renew(claim, self.clock()):
                            continue
                    except Exception:
                        pass  # Fail closed; never log provider/database exception payloads.
                    cancelled.set()
                    return

            heartbeat = Thread(target=renew_lease, daemon=True)
            heartbeat.start()
            try:
                result = handler(self.repository.get(claim), context)
                if not (self.stopped.is_set() or cancelled.is_set()):
                    if not isinstance(result, JobResult):
                        raise ValueError('Handler must explicitly report completion')
                    if result.completed:
                        self.repository.finish(claim, result.updates, self.clock())
                    else:
                        self.repository.defer(claim, self.clock(), result.delay_seconds)
            except Exception as exc:
                if not (self.stopped.is_set() or cancelled.is_set()):
                    self.repository.retry(claim, getattr(exc, 'code', None), self.clock())
            finally:
                done.set()
                heartbeat.join(timeout=self.renewal_interval + 1)
            return True
        return False
