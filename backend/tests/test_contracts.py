import json
from dataclasses import replace
import httpx
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.providers.defaults import (DefaultSectionParser, DummyKnowledgeRetriever, DefaultMemoryProvider,
    DefaultContextBuilder, MockSuggestionProvider, DisabledInquiryProvider, DefaultRevisionStrategy)
from app.providers.interfaces import SuggestionDraft
from app.providers.llm import GenericLLMSuggestionProvider, CompatibleLLMClient, ProviderError
from app.providers.config import ExperimentConfigFactory
from app.settings import Settings
from test_workflow import create, current, target, post, finish_round


@pytest.mark.parametrize('text,count', [
    ('无标题的教案。\n第二行。\n', 1), ('教学目标\n\n课堂小结\n', 2),
    ('# Intro\nText\n## Assessment\nCheck\n', 2), ('教学目标：理解分数\n课堂小结：回顾\n', 2),
    ('一、教学目标\r\n理解分数\r\n二、新知探究\r\n折纸\r\n', 2),
    ('\n前言\n教学目标\n目标\n', 2), ('\n'.join(f'## 单元 {i}\n内容' for i in range(80)), 80),
])
def test_parser_contract(text, count):
    drafts = DefaultSectionParser().parse(text)
    assert len(drafts) == count
    assert ''.join(d.content for d in drafts) == text
    assert all(d.title for d in drafts)


def test_empty_parser():
    with pytest.raises(ValueError): DefaultSectionParser().parse(' \n')


class History:
    def rejected_suggestions(self, session_id, section_id): return []


def context():
    section = {'id': 's', 'title': '目标', 'section_type': 'objectives', 'current_content': '理解分数'}
    memory = DefaultMemoryProvider(History()).build_memory({'id': 'session'}, {}, section)
    knowledge = DummyKnowledgeRetriever().retrieve(section, {})
    assert knowledge == []
    assert memory.latest_content == section['current_content']
    return section, DefaultContextBuilder().build(section, {'topic': '分数'}, memory, knowledge)


def test_default_contracts():
    section, ctx = context()
    assert ctx.user['lesson_metadata']['topic'] == '分数'
    assert DisabledInquiryProvider().inquire({}, section, '问', ctx) == []
    suggestions = MockSuggestionProvider().generate({}, {}, section, ctx)
    assert 0 <= len(suggestions) <= 2
    assert all(isinstance(x, SuggestionDraft) for x in suggestions)
    text = section['current_content']
    for suggestion in suggestions: text = DefaultRevisionStrategy().apply(text, suggestion)
    assert all(s.revision in text for s in suggestions)
    assert DefaultRevisionStrategy().apply(text, suggestions[0]) == text


@pytest.mark.parametrize('count', [0, 1, 2, 3])
@pytest.mark.parametrize('base_url,model', [('https://api.deepseek.com', 'deepseek-flash'),
    ('https://open.bigmodel.cn/api/paas/v4', 'glm-4.7-flash')])
def test_real_http_adapter_schema_and_count(count, base_url, model):
    section, ctx = context()
    draft = {'issue': '问题', 'reason': '原因', 'pedagogical_basis': '暂定依据', 'revision': '具体教学活动。'}
    def handler(request):
        assert str(request.url) == base_url + '/chat/completions'
        assert request.headers['Authorization'] == 'Bearer test-key'
        data = json.loads(request.content)
        assert data['model'] == model
        assert data['thinking'] == {'type': 'disabled'}
        assert data['messages'][0]['content'] == ctx.system
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'suggestions': [draft] * count})}}]})
    provider = GenericLLMSuggestionProvider(CompatibleLLMClient(base_url, model,
        'test-key', transport=httpx.MockTransport(handler), extra_body={'thinking': {'type': 'disabled'}}))
    if count > 2:
        with pytest.raises(ProviderError): provider.generate(ctx)
    else: assert len(provider.generate(ctx)) == count


@pytest.mark.parametrize('response', [None, {}, {'choices': []}, {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': '{bad json'}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"suggestions":[{"issue":""}]}'}}]}])
def test_malformed_llm(response):
    class Fake:
        def complete(self, ctx): return response
    section, ctx = context()
    with pytest.raises(ProviderError): GenericLLMSuggestionProvider(Fake()).generate(ctx)


@pytest.mark.parametrize('mode', ['timeout', 'http', 'network', 'key'])
def test_transport_errors(mode):
    def handler(request):
        if mode == 'timeout': raise httpx.ReadTimeout('timeout')
        if mode == 'network': raise httpx.ConnectError('network')
        return httpx.Response(429)
    client = CompatibleLLMClient('https://model.example/v1', 'model', '' if mode == 'key' else 'key',
                                 transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError): client.complete(context()[1])


def test_failure_persists_and_retry_is_recoverable(environment):
    sessions, factory = environment
    original_build = factory.build
    class Failing:
        def generate(self, *args): raise ProviderError('LLM_TIMEOUT', '超时')
    factory.build = lambda snapshot, history: replace(original_build(snapshot, history), suggestion_provider=Failing())
    with TestClient(create_app(sessions, factory)) as client:
        state, _ = create(client, '教学目标\n理解分数')
        post(client, state, '/suggestions', target(state), expected=502)
        restored = client.get(f"/api/sessions/{state['session']['id']}/current-state").json()
        assert current(restored)['review']['generated_at'] is None
        assert current(restored)['suggestions'] == []
        export = client.get(f"/api/sessions/{state['session']['id']}/export").json()
        assert export['generation_records'][0]['error_code'] == 'LLM_TIMEOUT'
        factory.build = original_build
        recovered = post(client, state, '/suggestions', target(state))
        assert len(current(recovered)['suggestions']) == 2


def test_config_snapshot_survives_server_provider_change(client, environment):
    state, _ = create(client)
    _, factory = environment
    factory.settings.suggestion_provider = 'generic_llm'
    generated = post(client, state, '/suggestions', target(state))
    assert current(generated)['suggestions'][0]['provider_type'] == 'mock'


def test_model_profiles_are_selectable_frozen_and_secrets_stay_server_side(environment):
    sessions, _ = environment
    settings = Settings(_env_file=None, suggestion_provider='generic_llm',
        llm_provider='deepseek', llm_api_key='deep-secret', zhipu_api_key='zhipu-secret',
        default_llm_profile='deepseek', context_contributors=[], llm_profiles=[
            {'id':'deepseek','label':'DeepSeek','provider':'deepseek',
             'base_url':'https://api.deepseek.com','model':'deepseek-chat'},
            {'id':'zhipu','label':'智谱 GLM','provider':'zhipu',
             'base_url':'https://open.bigmodel.cn/api/paas/v4','model':'glm-test'},
            {'id':'other','label':'Other','provider':'compatible',
             'base_url':'https://model.example/v1','model':'other-model'}])
    factory = ExperimentConfigFactory(settings)
    with TestClient(create_app(sessions, factory)) as client:
        capabilities = client.get('/api/capabilities')
        assert capabilities.status_code == 200
        profiles = capabilities.json()['model_profiles']
        assert [(x['id'], x['configured']) for x in profiles] == [
            ('deepseek', True), ('zhipu', True), ('other', False)]
        assert 'secret' not in capabilities.text
        request_key = '11111111-1111-4111-8111-111111111111'
        payload = {'request_key':request_key, 'metadata': {
            'subject':'数学','grade':'三年级','topic':'分数'}, 'content':'完整教案',
            'model_profile':'zhipu'}
        state = client.post('/api/sessions', json=payload).json()
        snapshot = state['session']['config_snapshot']
        assert snapshot['model_profile'] == 'zhipu'
        assert snapshot['model_label'] == '智谱 GLM'
        assert snapshot['model'] == 'glm-test'
        assert 'secret' not in json.dumps(snapshot)
        assert client.post('/api/sessions', json={**payload, 'model_profile':'deepseek'}).status_code == 409
        assert client.post('/api/sessions', json={**payload, 'request_key':'22222222-2222-4222-8222-222222222222',
            'model_profile':'missing'}).status_code == 422


def test_additional_profile_key_mapping():
    settings = Settings(_env_file=None, suggestion_provider='generic_llm', context_contributors=[],
        default_llm_profile='other', llm_api_keys={'other':'other-secret'}, llm_profiles=[{
            'id':'other','label':'Other','provider':'compatible',
            'base_url':'https://model.example/v1','model':'other-model'}])
    factory = ExperimentConfigFactory(settings)
    assert factory.public_profiles() == [{'id':'other','label':'Other','provider':'compatible',
        'model':'other-model','configured':True,'default':True}]
    snapshot = factory.snapshot('other')
    assert settings.api_key_for('other', snapshot) == 'other-secret'
    assert 'other-secret' not in json.dumps(snapshot)


def test_one_and_zero_suggestions_complete_round(environment):
    sessions, factory = environment
    original = factory.build
    class One:
        def generate(self, *args): return [SuggestionDraft(issue='问题', reason='原因', pedagogical_basis='暂定', revision='补充活动')]
    factory.build = lambda snapshot, history: replace(original(snapshot, history), suggestion_provider=One())
    with TestClient(create_app(sessions, factory)) as client:
        state, _ = create(client, '单元正文')
        state = finish_round(client, state)
        assert state['session']['status'] == 'ROUND_COMPLETED'
