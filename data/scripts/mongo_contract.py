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

# Separate additive inventory; v2 importers continue to use V2_SCHEMAS/INDEXES.
SUMMARY_EVIDENCE = {'bsonType':'object','required':['review_id','quote'],
    'properties':{'review_id':S,'quote':{'bsonType':'string','minLength':1,'maxLength':500}}}
SUMMARY_THEME = {'bsonType':'object','required':['id','description','issue_type','polarity','evidence'],
    'properties':{'id':S,'description':S,
        'issue_type':{'enum':['reported_defect','preference','feature_request','other']},
        'polarity':{'enum':['positive','negative','mixed','neutral']},
        'evidence':{'bsonType':'array','minItems':1,'maxItems':3,'items':SUMMARY_EVIDENCE}}}
SUMMARY_SEMANTIC_REVIEW = {'bsonType':'object','required':['status'],
    'properties':{'status':{'enum':['pending','approved','rejected']},
        'reviewer_id':S,'reviewer_type':{'enum':['human','automated']},
        'reviewed_at':D,'artifact_sha256':{'bsonType':'string','minLength':64,'maxLength':64},
        'rubric_version':S,'factual_support':{'bsonType':['bool','null']},
        'coverage':{'bsonType':['bool','null']},'classification':{'bsonType':['bool','null']}},
    'oneOf':[
        {'properties':{'status':{'enum':['pending']}}},
        {'required':['reviewer_id','reviewer_type','reviewed_at','artifact_sha256',
                     'rubric_version','factual_support','coverage','classification'],
         'properties':{'status':{'enum':['approved']},'factual_support':{'enum':[True]},
                       'coverage':{'enum':[True]},'classification':{'enum':[True]}}},
        {'required':['reviewer_id','reviewer_type','reviewed_at','artifact_sha256','rubric_version'],
         'properties':{'status':{'enum':['rejected']}}}]}
V3_SCHEMAS = {
 'product_summary_state': schema(['product_id'], dict(
    product_id=S, current_version={'bsonType':['int','long','null'],'minimum':1},
    next_version={'bsonType':['int','long'],'minimum':1},
    update_threshold={'bsonType':['int','long'],'minimum':1,'maximum':100},
    status={'enum':['uninitialized','waiting','queued','updating','ready','failed']},
    lease_expires_at=D, next_attempt_at=D, job_id=S,
    semantic_review=SUMMARY_SEMANTIC_REVIEW)),
 'product_summary_versions': schema(['product_id','version','parent_version','job_id','kind','narrative','themes','coverage','delta_review_ids','model_identity','prompt_version','guidance_references','created_at','semantic_review'], dict(
    product_id=S,version={'bsonType':['int','long'],'minimum':1},
    parent_version={'bsonType':['int','long','null'],'minimum':1},job_id=S,
    kind={'enum':['initial','reviews','guidance']},
    narrative={'bsonType':'string','minLength':1,'maxLength':4000},
    themes={'bsonType':'array','maxItems':30,'items':SUMMARY_THEME},
    coverage={'bsonType':'object','required':['historical_sample_count','new_review_count'],
        'properties':{'historical_sample_count':I,'new_review_count':I}},
    delta_review_ids={'bsonType':'array','maxItems':20,'items':S},
    manifest_ref={'bsonType':['string','null']},model_identity=S,prompt_version=S,
    guidance_references={'bsonType':'array','items':S},created_at=D,
    published_at={'bsonType':['date','null']},semantic_review=SUMMARY_SEMANTIC_REVIEW)),
 'product_summary_inputs': schema(['product_id','review_id','source','admitted_at','admission_sequence'], dict(
    product_id=S,review_id=S,source=S,admitted_at=D,
    admission_sequence={'bsonType':['int','long'],'minimum':0},
    incorporated_version={'bsonType':['int','long','null'],'minimum':1})),
}
V3_INDEXES = {
 'product_summary_state': [
    {'name':'one_state_per_product','keys':[('product_id',1)],'unique':True},
    {'name':'product_queue','keys':[('status',1),('next_attempt_at',1),('lease_expires_at',1)]}],
 'product_summary_versions': [
    {'name':'product_version','keys':[('product_id',1),('version',1)],'unique':True},
    {'name':'product_job','keys':[('product_id',1),('job_id',1)],'unique':True},
    {'name':'product_history','keys':[('product_id',1),('version',-1)]}],
 'product_summary_inputs': [
    {'name':'product_review','keys':[('product_id',1),('review_id',1)],'unique':True},
    {'name':'pending_product_inputs','keys':[('product_id',1),('incorporated_version',1),('admission_sequence',1),('_id',1)]}],
}
