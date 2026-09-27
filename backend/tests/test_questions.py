from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.errors import ServiceError
from tests.test_analysis import database, review, service, enqueue, work

class Answers:
    def __init__(self, result): self.result=result; self.calls=[]
    def answer(self, question, findings, evidence):
        self.calls.append((question, findings, evidence)); return self.result

def questions(db, model, **kwargs):
    from app.services.questions import QuestionService
    return QuestionService(db, model, **kwargs)

def completed(db):
    review(db); svc=service(db); run=enqueue(svc); work(svc,db); return run['id']

def test_scope_and_completion_are_required_before_provider(database):
    review(database); run=enqueue(service(database))
    model=Answers({}); svc=questions(database,model)
    for product, code in [('OTHER','run_not_found'),('P','run_not_completed')]:
        with pytest.raises(ServiceError) as exc: svc.answer(product,run['id'],'Why?')
        assert exc.value.code==code
    assert model.calls==[]

@pytest.mark.parametrize('evidence', [[{'review_id':'foreign','quote':'The hinge broke.'}], [{'review_id':'r1','quote':'hinge broke'}], []])
def test_unknown_or_modified_answer_citations_rejected(database,evidence):
    run=completed(database)
    with pytest.raises(ServiceError) as exc:
        questions(database,Answers({'answer':'Broken.','evidence':evidence,'insufficient_evidence':False})).answer('P',run,'Why?')
    assert exc.value.code=='invalid_answer_evidence'

def test_explicit_insufficiency_and_bounded_context(database):
    run=completed(database)
    result={'answer':'Insufficient evidence to determine the cause.','evidence':[],'insufficient_evidence':True}
    model=Answers(result)
    assert questions(database,model).answer('P',run,'What caused the break?')==result
    assert model.calls[0][2]==[{'review_id':'r1','quote':'The hinge broke.'}]
    with pytest.raises(ServiceError): questions(database,model,max_context_chars=10).answer('P',run,'Why?')
    with pytest.raises(ServiceError): questions(database,model).answer('P',run,' '*10)
    with pytest.raises(ServiceError): questions(database,model).answer('P',run,'x'*2001)

def test_pm_canonical_route_and_exact_shape(database,settings):
    from app.main import create_app
    run=completed(database)
    result={'answer':'One review reports a broken hinge.','evidence':[{'review_id':'r1','quote':'The hinge broke.'}],'insufficient_evidence':False}
    client=TestClient(create_app(settings,SimpleNamespace(questions=questions(database,Answers(result)))))
    path='/api/v1/products/P/questions'; payload={'run_id':run,'question':'What happened?'}
    assert client.post(path,json=payload).status_code==401
    assert client.post(path,json=payload,headers={'Authorization':'Bearer reviewer-secret-value'}).status_code==403
    response=client.post(path,json=payload,headers={'Authorization':'Bearer pm-secret-value'})
    assert response.status_code==200 and response.json()==result


def test_rate_limited_question_returns_explicit_retryable_error(database):
    from app.integrations.memory import ProviderError
    class Limited:
        def answer(self, *args): raise ProviderError('model_rate_limited')
    run = completed(database)
    with pytest.raises(ServiceError) as exc:
        questions(database, Limited()).answer('P', run, 'What happened?')
    assert exc.value.code == 'model_rate_limited'
    assert exc.value.status_code == 429
