from types import SimpleNamespace
import warnings
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("fail_close", [False, True])
def test_configured_app_closes_resources_on_lifespan(settings, monkeypatch, fail_close):
    from app.main import configured_app
    from app.config import Settings
    events=[]
    class Resource:
        def close(self): events.append('mongo')
    settings=replace(settings,llm_api_key='groq-secret',hindsight_api_url='https://memory.example',hindsight_api_key='memory-key')
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls:settings))
    monkeypatch.setattr('app.integrations.hindsight.HindsightMemory',lambda *a,**k:SimpleNamespace(close=lambda:events.append('memory')))
    def close_model():
        events.append('model')
        if fail_close: raise RuntimeError('close failed')
    monkeypatch.setattr('app.integrations.llm.DeepSeekModel',lambda key,**k:SimpleNamespace(close=close_model))
    monkeypatch.setattr('app.repositories.mongo.connect',lambda settings:(Resource(),object()))
    monkeypatch.setattr('app.repositories.capacity.CapacityGuard',lambda *a,**k:SimpleNamespace(check_ready=lambda:True))
    for name in ['analysis.AnalysisService','decisions.DecisionService','review_processing.ReviewProcessor']:
        monkeypatch.setattr('app.services.'+name,lambda *a,**k:SimpleNamespace(handle=lambda *a:None))
    monkeypatch.setattr('app.services.reviews.ReviewService',lambda *a,**k:None)
    monkeypatch.setattr('app.services.questions.QuestionService',lambda *a,**k:None)
    monkeypatch.setattr('app.repositories.jobs.JobRepository',lambda *a,**k:None)
    monkeypatch.setattr('app.worker.Worker',lambda *a,**k:SimpleNamespace(stop=lambda:events.append('worker')))
    with warnings.catch_warnings():
        warnings.simplefilter('error',DeprecationWarning)
        app=configured_app()
        from contextlib import nullcontext
        with pytest.raises(RuntimeError,match='close failed') if fail_close else nullcontext():
            with TestClient(app) as client:
                assert client.get('/health/live').status_code==200
        assert events==['worker','model','memory','mongo']


def test_worker_command_enters_lifespan_and_closes_it(monkeypatch):
    from contextlib import asynccontextmanager
    from threading import Event
    import app.run_worker as runner
    events=[]
    stopped=Event()
    def tick():
        events.append('tick')
        stopped.set()
        return True
    @asynccontextmanager
    async def lifespan(app):
        events.append('start')
        try: yield
        finally: events.append('close')
    app=SimpleNamespace(state=SimpleNamespace(worker=SimpleNamespace(stopped=stopped,tick=tick,stop=stopped.set)),
        router=SimpleNamespace(lifespan_context=lifespan,on_shutdown=[]))
    monkeypatch.setattr(runner,'configured_app',lambda:app)
    monkeypatch.setattr(runner.signal,'signal',lambda *a:None)
    runner.main()
    assert events==['start','tick','close']


def test_missing_groq_key_keeps_api_available_with_model_disabled(settings, monkeypatch):
    from app.main import configured_app
    from app.config import Settings
    monkeypatch.setattr(Settings, 'from_env', classmethod(lambda cls: settings))
    monkeypatch.setattr('app.integrations.llm.DeepSeekModel', lambda *a, **k: pytest.fail('Missing key must not construct provider'))
    monkeypatch.setattr('app.repositories.mongo.connect', lambda settings: (SimpleNamespace(close=lambda: None), object()))
    monkeypatch.setattr('app.repositories.capacity.CapacityGuard', lambda *a, **k: SimpleNamespace(check_ready=lambda: True))
    models = []
    def analysis(database, model, memory, **kwargs):
        models.append(model)
        return SimpleNamespace(handle=lambda *a: None)
    monkeypatch.setattr('app.services.analysis.AnalysisService', analysis)
    for name in ['decisions.DecisionService', 'review_processing.ReviewProcessor']:
        monkeypatch.setattr('app.services.' + name, lambda *a, **k: SimpleNamespace(handle=lambda *a: None))
    monkeypatch.setattr('app.services.reviews.ReviewService', lambda *a, **k: None)
    monkeypatch.setattr('app.services.questions.QuestionService', lambda *a, **k: None)
    monkeypatch.setattr('app.repositories.jobs.JobRepository', lambda *a, **k: None)
    monkeypatch.setattr('app.worker.Worker', lambda *a, **k: SimpleNamespace(stop=lambda: None))
    with TestClient(configured_app()) as client:
        assert client.get('/health/live').status_code == 200
    assert models == [None]
