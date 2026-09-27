"""Minimal MongoDB collection validators and query-driven index inventory."""
S = {'bsonType':'string','minLength':1}
D = {'bsonType':'date'}
B = {'bsonType':'bool'}
I = {'bsonType':['int','long'],'minimum':0}

def schema(required, properties):
    return {'$jsonSchema':{'bsonType':'object','required':['_id',*required],
                         'properties':{'_id':S,**properties}}}

SCHEMAS = {
 'products':schema(['title','provenance'],{'title':S,'provenance':{'bsonType':'object'}}),
 'reviews':schema(['parent_asin','asin','title','text','rating','timestamp','timestamp_ms','batch','batch_id','held_out','dataset_id','provenance'],
    dict(parent_asin=S,asin=S,title={'bsonType':'string'},text=S,rating={'bsonType':['double','int','long'],'minimum':1,'maximum':5},
         timestamp=D,timestamp_ms=I,batch={'enum':['A','B','C']},batch_id=S,held_out=B,dataset_id=S,
         helpful_vote={'bsonType':['int','long','null'],'minimum':0},verified_purchase={'bsonType':['bool','null']},provenance={'bsonType':'object'})),
 'batches':schema(['dataset_id','label','held_out','start_at','end_at','review_count','product_counts'],
    dict(dataset_id=S,label={'enum':['A','B','C']},held_out=B,start_at=D,end_at=D,review_count=I,product_counts={'bsonType':'object'})),
 'analysis_runs':schema(['parent_asin','batch_id','created_at','available_through','mode','status'],
    dict(parent_asin=S,batch_id=S,created_at=D,available_through=D,mode={'enum':['baseline','memory']},status={'enum':['pending','running','completed','failed']})),
 'findings':schema(['analysis_run_id','parent_asin','issue_type','description','review_ids'],
    dict(analysis_run_id=S,parent_asin=S,issue_type={'enum':['reported_defect','preference','feature_request','other']},description=S,
         review_ids={'bsonType':'array','minItems':1,'uniqueItems':True,'items':S})),
 'decisions':schema(['parent_asin','decided_at','available_through','kind','rationale'],
    dict(parent_asin=S,decided_at=D,available_through=D,kind=S,rationale=S)),
}
INDEXES = {
 'products':[],
 'reviews':[('product_time',[('parent_asin',1),('timestamp',1)]),
            ('product_batch_time',[('parent_asin',1),('batch_id',1),('timestamp',1)])],
 'batches':[],
 'analysis_runs':[('product_analysis_history',[('parent_asin',1),('created_at',-1)])],
 'findings':[('run_product_issue',[('analysis_run_id',1),('parent_asin',1),('issue_type',1)])],
 'decisions':[('product_decision_history',[('parent_asin',1),('decided_at',-1)])],
}
DATE_FIELDS={'reviews':['timestamp'],'batches':['start_at','end_at']}

# The original inventory remains stable for old importers and migration recognition.
# v2 adds live records without rewriting any imported record or its dataset identity.
from copy import deepcopy
V2_SCHEMAS = deepcopy(SCHEMAS)
legacy_review = deepcopy(SCHEMAS['reviews'])
legacy_review['$jsonSchema']['properties']['source'] = {'enum':['amazon_2023']}
live_review = schema(
 ['source','parent_asin','asin','title','text','rating','timestamp','timestamp_ms','created_at','author_id','version','provenance','processing','idempotency_key','payload_digest'],
 dict(source={'enum':['user_submission']},parent_asin=S,asin=S,title={'bsonType':'string','minLength':1,'maxLength':200},
      text={'bsonType':'string','minLength':1,'maxLength':10000},rating={'bsonType':['int','long'],'minimum':1,'maximum':5},
      timestamp=D,timestamp_ms=I,created_at=D,author_id=S,version={'enum':[1]},provenance={'bsonType':'object'},
      processing={'bsonType':'object','required':['status'],'properties':{'status':{'enum':['pending','running','completed','failed']}}},
      idempotency_key=S,payload_digest=S))
# Submitted reviews never masquerade as verified Amazon purchases or batch members.
V2_SCHEMAS['reviews'] = {'$or':[legacy_review,{'$and':[live_review,{'verified_purchase':{'$exists':False},'batch':{'$exists':False},'batch_id':{'$exists':False},'held_out':{'$exists':False}}]}]}
scoped_run = deepcopy(SCHEMAS['analysis_runs'])
scoped_run['$jsonSchema']['required'].remove('batch_id')
scoped_run['$jsonSchema']['required'].append('scope')
scoped_run['$jsonSchema']['properties']['scope']={'bsonType':'object','required':['source'],'properties':{'source':S}}
V2_SCHEMAS['analysis_runs']={'$or':[SCHEMAS['analysis_runs'],scoped_run]}
V2_SCHEMAS['rate_limits']=schema(['count','expires_at'],dict(count=I,expires_at=D))
V2_SCHEMAS['schema_migrations']=schema(['version','applied_at'],dict(version=I,applied_at=D))
IDEMPOTENCY_INDEX={'name':'submission_author_key','keys':[('author_id',1),('idempotency_key',1)],'unique':True,'partialFilterExpression':{'source':'user_submission'}}
V2_INDEXES={name:[{'name':n,'keys':keys} for n,keys in INDEXES.get(name,[])] for name in V2_SCHEMAS}
V2_INDEXES['reviews'] += [IDEMPOTENCY_INDEX,{'name':'product_time_cursor','keys':[('parent_asin',1),('timestamp',1),('_id',1)]}]
for name in ('reviews','decisions','analysis_runs'):
    V2_INDEXES[name].append({'name':'processing_queue','keys':[('processing.status',1),('processing.next_attempt_at',1),('processing.lease_expires_at',1)]})
V2_INDEXES['rate_limits']=[{'name':'rate_expiry','keys':[('expires_at',1)],'expireAfterSeconds':0}]
