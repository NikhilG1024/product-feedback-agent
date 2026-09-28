from contextlib import asynccontextmanager
from types import SimpleNamespace
from threading import Event
from pymongo.errors import AutoReconnect
from app import run_summary_worker as runner


def test_database_interruption_does_not_stop_worker(monkeypatch):
    class Worker:
        stopped = Event()
        calls = 0
        def stop(self): self.stopped.set()
        def tick(self):
            self.calls += 1
            if self.calls == 1:
                raise AutoReconnect('temporary database outage')
            self.stop()
            return True
    worker = Worker()
    @asynccontextmanager
    async def lifespan(app):
        yield
    app = SimpleNamespace(state=SimpleNamespace(summary_worker=worker),
                          router=SimpleNamespace(lifespan_context=lifespan))
    monkeypatch.setattr(runner, 'configured_app', lambda: app)
    monkeypatch.setattr(runner.signal, 'signal', lambda *args: None)
    runner.main()
    assert worker.calls == 2
