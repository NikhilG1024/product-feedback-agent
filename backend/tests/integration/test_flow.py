"""Acceptance through HTTP + Worker.tick; real Mongo, deterministic provider doubles.

This verifies orchestration and provenance, not live provider quality.
"""
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.domain import FindingDraft, Evidence, MemoryContext
from app.main import create_app
from app.repositories.jobs import JobRepository
from app.services.analysis import AnalysisService
from app.services.decisions import DecisionService
from app.services.questions import QuestionService
from app.services.review_processing import ReviewProcessor
from app.services.reviews import ReviewService
from app.worker import Worker
from tests.test_analysis import database


class DeterministicMemory:
    def __init__(self): self.banks={}
    def ensure_retained(self, bank_id, record, *, state, checkpoint):
        self.banks.setdefault(bank_id,{})[record.id]=record
        checkpoint({'record_id':record.id,'status':'completed'})
    def recall(self, bank_id, query):
        records=self.banks[bank_id]
        return MemoryContext(text='\n'.join(r.content for r in records.values()),record_ids=list(records))


class DeterministicModel:
    def extract(self, product, reviews, context):
        corrected='Treat stiffness as a preference' in context.text
        return [FindingDraft(issue_type='preference' if corrected else 'reported_defect',
            theme='hinge stiffness',description='Stiff hinge reported.',
            evidence=[Evidence(review_id=r['_id'],quote=r['text']) for r in reviews])]
    def answer(self, question, findings, evidence):
        return {'answer':'Reviewers describe hinge stiffness; the PM treats it as a preference.',
                'evidence':evidence,'insufficient_evidence':False}


def test_submit_retain_analyze_correct_reanalyze_and_grounded_question(database,settings):
    model, memory=DeterministicModel(),DeterministicMemory()
    reviews=ReviewService(database)
    analysis=AnalysisService(database,model,memory)
    decisions=DecisionService(database,memory)
    processor=ReviewProcessor(database,model,memory)
    services=SimpleNamespace(reviews=reviews,analysis=analysis,decisions=decisions,
                             questions=QuestionService(database,model))
    worker=Worker(JobRepository(database),{'reviews':processor.handle,
        'analysis_runs':analysis.handle,'decisions':decisions.handle})
    reviewer={'Authorization':'Bearer reviewer-secret-value'}
    pm={'Authorization':'Bearer pm-secret-value'}
    with TestClient(create_app(settings,services)) as client:
        def submit(key,text):
            response=client.post('/api/v1/products/P/reviews',headers={**reviewer,'Idempotency-Key':key},
                json={'title':'Hinge','text':text,'rating':3})
            assert response.status_code==201,response.text
            return response.json()['id']
        def status(key):
            return client.get('/api/v1/reviews/'+key+'/status',headers=reviewer).json()['processing']
        def analyze():
            response=client.post('/api/v1/products/P/analysis-runs',headers=pm,
                json={'mode':'memory','scope':{'source':'user_submission'}})
            assert response.status_code==202,response.text
            run=response.json()['id']
            assert worker.tick()
            report=client.get('/api/v1/analysis-runs/'+run,headers=pm).json()
            assert report['status']=='completed',report
            findings=client.get('/api/v1/products/P/findings',params={'run_id':run},headers=pm).json()['items']
            return run,report,findings
        first=submit('flow-1','The hinge feels stiff.')
        assert status(first)['status']=='pending'
        # A second API instance recovers the persisted submission after a lost response.
        with TestClient(create_app(settings,SimpleNamespace(reviews=ReviewService(database)))) as restarted:
            saved=restarted.get('/api/v1/reviews/'+first+'/status',headers=reviewer)
            assert saved.status_code==200
        assert worker.tick()
        assert status(first)['memory_status']=='synced'
        assert status(first)['status']=='completed'
        old_run,old_report,old_findings=analyze()
        assert old_report['denominator']==1 and old_report['guidance_references']==[]
        assert old_findings[0]['issue_type']=='reported_defect'
        response=client.post('/api/v1/products/P/decisions',headers=pm,json={
            'kind':'correction','rationale':'Treat stiffness as a preference, not an established defect.',
            'evidence_ids':[first]})
        assert response.status_code==201,response.text
        correction=response.json()['id']
        assert worker.tick()
        assert client.get('/api/v1/products/P/decisions',headers=pm).json()['items'][0]['processing']['memory_status']=='synced'
        second=submit('flow-2','The hinge is stiff but works.')
        assert worker.tick()
        items=client.get('/api/v1/products/P/reviews',headers=pm).json()['items']
        later=next(r for r in items if r['id']==second)
        assert later['guidance_references']==[correction]
        assert later['provisional_findings'][0]['issue_type']=='preference'
        new_run,new_report,new_findings=analyze()
        assert new_report['denominator']==2
        assert new_report['guidance_references'][0]['decision_id']==correction
        assert new_findings[0]['issue_type']=='preference'
        assert new_findings[0]['supporting_review_count']==2
        eligible={r['id']:r['text'] for r in items}
        for finding in new_findings:
            assert finding['provenance_validated'] is True
            assert finding['semantic_support']=='model_interpretation'
            for citation in finding['evidence']:
                assert citation['quote'] in eligible[citation['review_id']]
        unchanged=client.get('/api/v1/analysis-runs/'+old_run,headers=pm).json()
        assert unchanged['stale'] is True
        assert {k:v for k,v in unchanged.items() if k!='stale'}=={k:v for k,v in old_report.items() if k!='stale'}
        assert client.get('/api/v1/products/P/findings',params={'run_id':old_run},headers=pm).json()['items']==old_findings
        response=client.post('/api/v1/products/P/questions',headers=pm,
            json={'run_id':new_run,'question':'What do reviews say about the hinge?'})
        assert response.status_code==200,response.text
        assert response.json()['insufficient_evidence'] is False
        assert response.json()['evidence']==new_findings[0]['evidence']
