import json
import httpx
import pytest
from app.config import FREE_LLM_MODEL, FREE_LLM_URL, Settings
from app.integrations.groq_summary import GroqSummaryModel
from app.integrations.memory import ProviderError
from app.summaries.contracts import CompactGeneratedSummary
from app.summaries.generation import _prompt_messages

MESSAGES = _prompt_messages({'id':'P','title':'Headphones','product_type':None},None,
    [{'id':'r1','title':'Battery','text':'Battery lasts all day.','rating':5}],[])
OUTPUT = {'narrative':'Battery life is praised.','themes':[{'id':'battery',
    'description':'Long battery life','issue_type':'preference','polarity':'positive',
    'evidence':[{'review_id':'r1','quote':'Battery lasts all day.'}]}], 'contradictions':[]}
def response(output=OUTPUT, reason='stop'):
    return httpx.Response(200,json={'choices':[{'finish_reason':reason,
        'message':{'content':json.dumps(output)}}]})

def test_pinned_groq_json_mode_and_budget():
    seen=[]
    model=GroqSummaryModel('secret',transport=httpx.MockTransport(lambda r:seen.append(r) or response()))
    assert model.generate_summary(MESSAGES,CompactGeneratedSummary)==OUTPUT
    payload=json.loads(seen[0].content)
    assert str(seen[0].url)==FREE_LLM_URL+'/chat/completions'
    assert payload['model']==FREE_LLM_MODEL
    assert payload['response_format']=={'type':'json_object'}
    assert payload['reasoning_effort']=='low' and payload['include_reasoning'] is False
    assert payload['max_completion_tokens']==3500
    assert model.summary_prompt_budget_bytes>13000
    assert 'never 4 or more' in payload['messages'][0]['content']

@pytest.mark.parametrize('kwargs',[{'model':'openai/gpt-oss-120b'},
                                    {'base_url':'https://openrouter.ai/api/v1'}])
def test_no_fallback(kwargs):
    with pytest.raises(ValueError,match='free-plan Groq'):
        GroqSummaryModel('secret',**kwargs)

def test_missing_key_and_rate_limit():
    with pytest.raises(ProviderError,match='provider_not_configured'):
        GroqSummaryModel('')
    model=GroqSummaryModel('secret',transport=httpx.MockTransport(lambda _:httpx.Response(429)))
    with pytest.raises(ProviderError,match='^model_rate_limited$'):
        model.generate_summary(MESSAGES,CompactGeneratedSummary)

def test_truncated_distinct_from_invalid_schema():
    model=GroqSummaryModel('secret',transport=httpx.MockTransport(lambda _:response(reason='length')))
    with pytest.raises(ProviderError,match='^model_output_truncated$'):
        model.generate_summary(MESSAGES,CompactGeneratedSummary)
    model=GroqSummaryModel('secret',transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
        'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]})))
    with pytest.raises(ProviderError,match='^model_invalid_output$'):
        model.generate_summary(MESSAGES,CompactGeneratedSummary)

def test_compacts_old_representatives_but_rejects_four_fresh_ids():
    old=[{'review_id':f'old{i}','quote':f'old quote {i}'} for i in range(3)]
    output={**OUTPUT,'themes':[{**OUTPUT['themes'][0],
        'evidence':[*old,OUTPUT['themes'][0]['evidence'][0]]}]}
    model=GroqSummaryModel('secret',transport=httpx.MockTransport(lambda _:response(output)))
    kept=model.generate_summary(MESSAGES,CompactGeneratedSummary)['themes'][0]['evidence']
    assert kept[0]['review_id']=='r1' and len(kept)==3
    reviews=[{'id':f'r{i}','title':'Title','text':f'Text {i}','rating':5} for i in range(4)]
    messages=_prompt_messages({'id':'P','title':'Headphones','product_type':None},None,reviews,[])
    output={**OUTPUT,'themes':[{**OUTPUT['themes'][0], 'evidence':[
        {'review_id':f'r{i}','quote':f'Text {i}'} for i in range(4)]}]}
    model=GroqSummaryModel('secret',transport=httpx.MockTransport(lambda _:response(output)))
    with pytest.raises(ProviderError,match='^model_invalid_output$'):
        model.generate_summary(messages,CompactGeneratedSummary)

def test_summary_provider_config_groq_only():
    base={'mongo_uri':'mongodb://127.0.0.1:27032','mongo_database':'test_groq_summary',
          'reviewer_token':'reviewer','pm_token':'pm'}
    assert Settings(**base,groq_api_key='secret',summary_llm_provider='groq').summary_llm_provider=='groq'
    with pytest.raises(ValueError,match='Unsupported summary provider'):
        Settings(**base,summary_llm_provider='openrouter')
