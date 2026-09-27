"""Browser contract fixture: real FastAPI routes, deterministic in-memory services.
No MongoDB, Hindsight, LLM, .env, or real bearer tokens are used.
Run with PYTHONPATH=backend backend/.venv/bin/python frontend/tests/contract_server.py.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import uvicorn
from app.main import create_app
from app.config import Settings
from app.errors import ServiceError

NOW = datetime.now(timezone.utc)
PRODUCTS = [{'id': f'contract-{i}', 'title': f'Contract test product {i}', 'product_type': 'Test category'} for i in range(35)]
PROCESSING = {'status': 'pending', 'attempts': 0, 'memory_status': 'pending', 'classification_status': 'pending', 'error_code': None}

def pm(principal):
    if principal.role != 'pm': raise ServiceError('forbidden',403)

class Reviews:
    def __init__(self): self.saved = {}; self.keys = {}
    def products(self,cursor,limit):
        offset=int(cursor or 0); return {'items':PRODUCTS[offset:offset+limit], 'next_cursor':str(offset+limit) if offset+limit<len(PRODUCTS) else None}
    def product(self,p): return next(x for x in PRODUCTS if x['id']==p)
    def list(self,p,principal,source,batch,cursor,limit):
        if source=='user_submission': return {'items':[], 'next_cursor':None}
        return {'items':[{'id':'contract-review','parent_asin':p,'asin':p,'title':'Battery concern','text':'The battery only lasts two hours.','rating':2,'timestamp':NOW,'source':'amazon_2023','batch_id':'contract:A','processing':None}], 'next_cursor':None}
    def submit(self,p,principal,key,payload):
        if principal.role!='reviewer': raise ServiceError('forbidden',403)
        if key not in self.keys:
            result={'id':str(uuid4()),'processing':PROCESSING};self.saved[result['id']]=result;self.keys[key]=result
        return self.keys[key]
    def status(self,id,principal): return self.saved[id]

class Analysis:
    def __init__(self): self.runs={}
    def enqueue(self,p,principal,payload):
        pm(principal);id=str(uuid4());r={'id':id,'parent_asin':p,'mode':payload.mode,'scope':payload.scope.model_dump(),'status':'pending','error_code':None,'created_at':NOW,'available_through':NOW,'snapshot_hash':'contract-snapshot','denominator':1,'stale':False,'summary':None,'supporting_review_counts':None,'guidance_references':None,'trend':None,'limitations':None,'investigation_suggestions':None};self.runs[id]=r;return dict(r)
    def get(self,id):
        r=self.runs[id];r.update(status='completed',summary='One review reports a short battery runtime.',supporting_review_counts=[{'finding_id':'contract-finding','theme':'Short battery runtime','supporting_review_count':1}],guidance_references=[],limitations=['Contract fixture, not real AI output.'],investigation_suggestions=[]);return r
    def findings(self,p,id): return {'run_id':id,'denominator':1,'items':[{'id':'contract-finding','theme':'Short battery runtime','issue_type':'reported_defect','description':'Customer report','evidence':[{'review_id':'contract-review','quote':'The battery only lasts two hours.'}],'review_ids':['contract-review'],'supporting_review_count':1,'provenance_validated':True,'semantic_support':'model_interpretation','evidence_sampled':False}]}

class Decisions:
    def __init__(self): self.items=[]
    def create(self,p,principal,payload):
        pm(principal);d={'id':str(uuid4()),'parent_asin':p,**payload.model_dump(),'decided_at':NOW,'available_through':NOW,'processing':PROCESSING};self.items.append(d);return d
    def list(self,p,principal,limit):pm(principal);return {'items':self.items[-limit:]}

class Questions:
    def answer(self,p,run,question): return {'answer':'The review reports a two-hour battery runtime.','evidence':[{'review_id':'contract-review','quote':'The battery only lasts two hours.'}],'insufficient_evidence':False}

settings=Settings(mongo_uri='mongodb://unused',mongo_database='unused',reviewer_token='fixture-reviewer',pm_token='fixture-pm')
app=create_app(settings,SimpleNamespace(reviews=Reviews(),analysis=Analysis(),decisions=Decisions(),questions=Questions()))
from app.api.questions import router as questions_router
if not any(getattr(route, 'path', '') == '/api/v1/products/{product_id}/questions' for route in app.routes):
    app.include_router(questions_router)
if __name__=='__main__': uvicorn.run(app,host='127.0.0.1',port=8001,log_level='error')
