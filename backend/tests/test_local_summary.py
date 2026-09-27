import json
import httpx
import pytest
from app.config import Settings
from app.integrations.local_summary import LocalSummaryModel, LOCAL_SUMMARY_MODEL
from app.integrations.memory import ProviderError
from app.summaries.contracts import CompactGeneratedSummary
from app.summaries.generation import _prompt_messages

MESSAGES=_prompt_messages({'id':'P','title':'Headphones','product_type':None},None,
    [{'id':'r1','title':'Battery','text':'Battery lasts all day.','rating':5}],[])
OUTPUT={'narrative':'Battery life is praised.','themes':[{'id':'battery',
    'description':'Long battery life','issue_type':'preference','polarity':'positive',
    'evidence':[{'review_id':'r1','quote':'Battery lasts all day.'}]}],'contradictions':[]}
def response(output=OUTPUT,reason='stop'):
    return httpx.Response(200,json={'choices':[{'finish_reason':reason,
        'message':{'content':json.dumps(output)}}]})

def test_pinned_schema_request_and_auth_header():
    seen=[]
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda request:seen.append(request) or response()))
    assert model.generate_summary(MESSAGES,CompactGeneratedSummary)==OUTPUT
    payload=json.loads(seen[0].content)
    assert str(seen[0].url)=='http://127.0.0.1:4300/v1/chat/completions'
    assert seen[0].headers['Authorization']=='Bearer secret'
    assert seen[0].headers['ngrok-skip-browser-warning']=='true'
    assert payload['model']==LOCAL_SUMMARY_MODEL
    assert payload['response_format']['type']=='json_object'
    assert payload['response_format']['schema']['properties']['narrative']['maxLength']==900
    themes=payload['response_format']['schema']['properties']['themes']
    assert themes['minItems']==themes['maxItems']==1
    assert themes['prefixItems'][0]['properties']['evidence']['items']['properties']['review_id']['const']=='r1'
    assert 'Battery lasts all day.' in themes['prefixItems'][0]['properties']['evidence']['items']['properties']['quote']['enum']
    assert payload['temperature']==0 and payload['max_tokens']==6144
    assert model.summary_prompt_budget_bytes>30000

@pytest.mark.parametrize('url',['https://example.com/v1','http://example.com/v1',
    'https://x.ngrok.app/other','https://user:secret@x.ngrok.app/v1',
    'https://x.ngrok.app/v1?token=secret','http://0.0.0.0:8097/v1'])
def test_rejects_unapproved_endpoint(url):
    with pytest.raises(ValueError):LocalSummaryModel('secret',base_url=url)

def test_accepts_https_ngrok_and_rejects_other_model():
    model=LocalSummaryModel('secret',base_url='https://pfia.ngrok-free.app/v1',
        transport=httpx.MockTransport(lambda _:response()))
    assert model.generate_summary(MESSAGES,CompactGeneratedSummary)==OUTPUT
    with pytest.raises(ValueError,match='pinned local Qwen'):
        LocalSummaryModel('secret',base_url='https://pfia.ngrok-free.app/v1',model='other')

def test_missing_key_truncation_and_invalid_schema_fail_closed():
    with pytest.raises(ProviderError,match='provider_not_configured'):
        LocalSummaryModel('',base_url='http://127.0.0.1:4300/v1')
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response(reason='length')))
    with pytest.raises(ProviderError,match='model_output_truncated'):
        model.generate_summary(MESSAGES,CompactGeneratedSummary)
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response({**OUTPUT,'narrative':'x'*901})))
    with pytest.raises(ProviderError,match='model_invalid_output'):
        model.generate_summary(MESSAGES,CompactGeneratedSummary)

def test_local_default_and_explicit_groq_selection():
    base={'mongo_uri':'mongodb://127.0.0.1:27032','mongo_database':'test_local_summary',
          'reviewer_token':'reviewer','pm_token':'pm'}
    assert Settings(**base).summary_llm_provider=='local'
    assert Settings(**base,summary_llm_provider='groq').summary_llm_provider=='groq'
    with pytest.raises(ValueError,match='Unsupported summary provider'):
        Settings(**base,summary_llm_provider='openrouter')


def test_three_review_grammar_forces_coverage_and_exact_source_quotes():
    reviews=[{'id':'a','title':'test','text':'test','rating':3},
             {'id':'b','title':'23456','text':'qwertyu','rating':3},
             {'id':'c','title':'bad product','text':'very worst product i have used','rating':1}]
    messages=_prompt_messages({'id':'P','title':'Product','product_type':None},None,
                              reviews,[],delta_mode=True)
    seen=[]
    output={**OUTPUT,'themes':[{**OUTPUT['themes'][0], 'id':r['id'],
        'issue_type':'other' if r['id']=='a' else 'preference',
        'evidence':[{'review_id':r['id'],'quote':r['text']}]} for r in reviews]}
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda request:seen.append(request) or response(output)))
    assert len(model.generate_summary(messages,CompactGeneratedSummary)['themes'])==3
    themes=json.loads(seen[0].content)['response_format']['schema']['properties']['themes']
    assert themes['minItems']==themes['maxItems']==3
    assert [item['properties']['evidence']['items']['properties']['review_id']['const']
            for item in themes['prefixItems']]==['a','b','c']
    assert themes['prefixItems'][1]['properties']['evidence']['items']['properties']['quote']['enum']==[
        '23456','qwertyu']
    assert 'placeholder review' in messages[0]['content']
    reversed_output={**output,'themes':list(reversed(output['themes']))}
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response(reversed_output)))
    assert len(model.generate_summary(messages,CompactGeneratedSummary)['themes'])==3
    missing={**output,'themes':output['themes'][1:]}
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response(missing)))
    with pytest.raises(ProviderError,match='model_invalid_output'):
        model.generate_summary(messages,CompactGeneratedSummary)


def test_grouped_fresh_citations_and_duplicate_review_across_themes_are_accepted():
    reviews=[{'id':key,'title':f'Title {key}','text':f'Exact report {key}.','rating':3}
             for key in ('a','b','c','d')]
    messages=_prompt_messages({'id':'P','title':'Product','product_type':None},None,
                              reviews,[],delta_mode=True)
    grouped={**OUTPUT,'themes':[
        {**OUTPUT['themes'][0], 'id':'shared', 'evidence':[
            {'review_id':key,'quote':f'Exact report {key}.'} for key in ('a','b','c')]},
        {**OUTPUT['themes'][0], 'id':'quality', 'evidence':[
            {'review_id':key,'quote':f'Exact report {key}.'} for key in ('c','d')]}]}
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response(grouped)))
    assert model.generate_summary(messages,CompactGeneratedSummary)==grouped


def test_grouped_fresh_citations_still_reject_missing_or_unsupported_sources():
    reviews=[{'id':key,'title':f'Title {key}','text':f'Exact report {key}.','rating':3}
             for key in ('a','b','c','d')]
    messages=_prompt_messages({'id':'P','title':'Product','product_type':None},None,
                              reviews,[],delta_mode=True)
    evidence=[{'review_id':key,'quote':f'Exact report {key}.'} for key in ('a','b','c')]
    for altered in (evidence, evidence+[{'review_id':'d','quote':'Invented quote'}],
                    evidence+[{'review_id':'unknown','quote':'Exact report d.'}]):
        invalid={**OUTPUT,'themes':[{**OUTPUT['themes'][0], 'evidence':altered}]}
        model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
            transport=httpx.MockTransport(lambda _,result=invalid:response(result)))
        with pytest.raises(ProviderError,match='model_invalid_output'):
            model.generate_summary(messages,CompactGeneratedSummary)


def test_internal_exact_source_substring_is_valid_even_when_grammar_enum_omits_it():
    text='Opening context. The exact cited sentence sits in the middle. Closing context.'
    messages=_prompt_messages({'id':'P','title':'Product','product_type':None},None,
        [{'id':'r1','title':'Review','text':text,'rating':4}],[],delta_mode=True)
    output={**OUTPUT,'themes':[{**OUTPUT['themes'][0],
        'evidence':[{'review_id':'r1','quote':'The exact cited sentence sits in the middle.'}]}]}
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response(output)))
    assert model.generate_summary(messages,CompactGeneratedSummary)==output
    altered={**output,'themes':[{**output['themes'][0],
        'evidence':[{'review_id':'r1','quote':'The cited sentence sits in the middle.'}]}]}
    model=LocalSummaryModel('secret',base_url='http://127.0.0.1:4300/v1',
        transport=httpx.MockTransport(lambda _:response(altered)))
    with pytest.raises(ProviderError,match='model_invalid_output'):
        model.generate_summary(messages,CompactGeneratedSummary)
