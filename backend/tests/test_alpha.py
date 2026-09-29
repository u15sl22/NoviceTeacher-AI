import json
from dataclasses import replace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app import models as m
from app.api import create_app
from app.auth import DevelopmentAuthProvider
from app.context import ContextFragment, ContextAssembler
from app.providers.alpha import BoundedSectionParser
from app.providers.interfaces import SuggestionDraft
from app.providers.llm import ProviderError
from app.retrieval import MetadataRetriever, RetrievalQuery
from app.dataset import import_dataset, verify_annotation, promote_annotation
from test_workflow import create, target, current, post, finish_round


def test_users_cannot_read_mutate_export_or_chat_each_other(environment):
    sessions, factory = environment
    with TestClient(create_app(sessions,factory,DevelopmentAuthProvider('alice'))) as alice, \
         TestClient(create_app(sessions,factory,DevelopmentAuthProvider('bob'))) as bob:
        state, payload = create(alice)
        sid = state['session']['id']
        owner = alice.get('/api/me').json()['id']
        assert state['session']['user_id'] == owner
        for suffix in ['/current-state','/export','/chat','/final']:
            assert bob.get('/api/sessions/'+sid+suffix).status_code == 404
        assert bob.post('/api/sessions/'+sid+'/suggestions',json=target(state)).status_code==404
        assert bob.post('/api/sessions/'+sid+'/terminate').status_code==404
        assert bob.get('/api/sessions').json()==[]
        other=bob.post('/api/sessions',json=payload).json()
        assert other['session']['id'] != sid  # Same key is independent per user.
        assert len(alice.get('/api/sessions').json())==1
        exported=alice.get('/api/sessions/'+sid+'/export').json()
        assert exported['chat_messages'][0]['content']==payload['content']
        assert all(v['user_id']==owner for v in exported['section_versions'])


@pytest.mark.parametrize('enabled',[[],['lesson_context'],['session_memory'],['case_retrieval'],
    ['knowledge_retrieval'],['preferences'],['lesson_context','case_retrieval','knowledge_retrieval'],
    ['lesson_context','session_memory','knowledge_retrieval'],['lesson_context','session_memory','case_retrieval']])
def test_configurable_pipeline_vertical_slice(environment, enabled):
    sessions, factory=environment
    factory.settings.context_contributors=enabled
    with TestClient(create_app(sessions,factory)) as client:
        state,_=create(client,'教学目标\n理解分数')
        state=finish_round(client,state)
        state=post(client,state,'/terminate')
        exported=client.get('/api/sessions/'+state['session']['id']+'/export').json()
        assert exported['context_snapshots'][0]['contributor_names']==['current_section']+sorted(enabled,
            key=lambda x: ['lesson_context','session_memory','knowledge_retrieval','case_retrieval','preferences'].index(x))
        assert len(exported['retrieval_records'])==sum(x.endswith('_retrieval') for x in enabled)


def test_new_contributor_registry_and_frozen_config(environment):
    sessions,factory=environment
    class Extra:
        def contribute(self, request): return ContextFragment(contributor_type='extra',content={'hint':'test'},source_ids=['example'])
    factory.registry.register('extra',lambda **kwargs:Extra())
    factory.settings.context_contributors=['extra']
    with TestClient(create_app(sessions,factory)) as client:
        state,_=create(client,'完整教案')
        factory.settings.context_contributors=[]
        state=post(client,state,'/suggestions',target(state))
        exported=client.get('/api/sessions/'+state['session']['id']+'/export').json()
        assert exported['context_snapshots'][0]['contributor_names']==['current_section','extra']
        other,_=create(client,'另一教案')
        assert other['session']['config_snapshot']['context_contributors']==[]


def test_parser_bound_and_lossless():
    text='\n'.join('## 单元'+str(i)+'\n教学正文' for i in range(80))
    parts=BoundedSectionParser().parse(text)
    assert len(parts)==10
    assert ''.join(x.content for x in parts)==text


def test_context_budget_preserves_required_and_prioritizes_evidence():
    required=ContextFragment(contributor_type='current_section',content={'text':'正文'},required=True,priority=0,retention_priority=0)
    memory=ContextFragment(contributor_type='session_memory',content={'text':'m'*6000},retention_priority=40)
    knowledge=ContextFragment(contributor_type='knowledge',content={'text':'verified'},retention_priority=20)
    ctx=ContextAssembler(4000).assemble([required,memory,knowledge])
    assert [x['contributor_type'] for x in ctx.user['fragments']]==['current_section','knowledge']
    assert 'session_memory' in ctx.user['budget']['omitted']
    with pytest.raises(ProviderError):
        ContextAssembler(1000).assemble([required.model_copy(update={'content':{'text':'长'*5000}})])


def fixtures(db):
    import_dataset(db,[{'id':'lesson','source_file':'lesson.docx','学科':'数学','年级':'三年级上册','课题名称':'分数'}],
        [{'lesson':'lesson','dimension':'教学目标','原文引用':'理解分数','评价':'目标缺少观察指标','具体分析':'分数理解需要证据','建议':'说明分数含义'}], 'test')
    return db.scalar(select(m.DatasetAnnotation))


def test_raw_dataset_import_idempotent_and_verified_promotion(environment):
    sessions,_=environment
    with sessions.begin() as db:
        annotation=fixtures(db); fixtures(db)
        assert db.scalar(select(func.count()).select_from(m.DatasetAnnotation))==1
        query=RetrievalQuery(subject='小学数学',grade='三年级',topic='分数',section_content='理解分数')
        assert MetadataRetriever(db,'case').retrieve(query)==[]
        with pytest.raises(ValueError): promote_annotation(db,annotation.id)
        reviewer=DevelopmentAuthProvider('reviewer').get_current_user(db)
        checks={x:True for x in ['issue_exists','location_correct','suggestion_actionable','basis_supported','worth_fixing']}
        verify_annotation(db,annotation.id,reviewer,checks,'人工核对原文、问题及来源','verified')
        case=promote_annotation(db,annotation.id,'objectives','objective_measurability')
        assert promote_annotation(db,annotation.id).id==case.id
        assert MetadataRetriever(db,'case').retrieve(query)[0].id==case.id
        verify_annotation(db,annotation.id,reviewer,{**checks,'basis_supported':False},'依据待进一步核验','rejected')
        assert MetadataRetriever(db,'case').retrieve(query)==[]
        assert annotation.raw_payload['评价']=='目标缺少观察指标'


def test_knowledge_citation_provenance_and_unknown_citation_failure(environment):
    sessions,factory=environment
    with sessions.begin() as db:
        for status in ['raw','verified']:
            item=m.KnowledgeItem(content='分数理解需要学生解释和展示',source='人工核验示例',source_type='test',source_locator='p.1',
                subject='数学',grade='三年级上册',topic='分数',section_type='objectives',verification_status=status)
            db.add(item); db.flush()
        verified_id=item.id
    original=factory.build
    class Citing:
        def generate(self,context):
            return [SuggestionDraft(issue='问题',reason='原因',pedagogical_basis='依据说明',revision='学生解释分数',knowledge_ids=[verified_id])]
    factory.build=lambda snapshot,history:replace(original(snapshot,history),suggestion_provider=Citing())
    with TestClient(create_app(sessions,factory)) as client:
        state,_=create(client,'教学目标\n理解分数')
        state=post(client,state,'/suggestions',target(state))
        suggestion=current(state)['suggestions'][0]
        assert suggestion['basis_type']=='verified_source'
        assert suggestion['basis_sources'][0]['source_locator']=='p.1'
        exported=client.get('/api/sessions/'+state['session']['id']+'/export').json()
        assert exported['context_snapshots'][0]['knowledge_ids']==[verified_id]
        assert len(exported['retrieval_records'])==2
        factory.settings.context_contributors=[]
        another,_=create(client,'完整教案')
        post(client,another,'/suggestions',target(another),expected=502)
        export=client.get('/api/sessions/'+another['session']['id']+'/export').json()
        assert export['suggestions']==[]
        assert export['failures'][0]['error_code']=='INVALID_CITATION'


def test_replace_and_unsafe_candidate_requires_explicit_confirmation(environment):
    sessions,factory=environment
    original=factory.build
    class Replacing:
        def generate(self,context):
            return [SuggestionDraft(issue='目标',reason='原因',pedagogical_basis='暂定',revision='学生能说明分数含义',
                     revision_mode='replace',target_text='理解分数'),
                    SuggestionDraft(issue='补充',reason='原因',pedagogical_basis='暂定',revision='补充练习活动',
                     revision_mode='replace',target_text='理解分数')]
    factory.build=lambda snapshot,history:replace(original(snapshot,history),suggestion_provider=Replacing())
    with TestClient(create_app(sessions,factory)) as client:
        state,_=create(client,'教学目标\n理解分数')
        state=post(client,state,'/suggestions',target(state))
        first,second=current(state)['suggestions']
        state=post(client,state,f"/suggestions/{first['id']}/decision",{'decision':'ACCEPT'})
        assert '理解分数' not in current(state)['current_content']
        state=post(client,state,f"/suggestions/{second['id']}/decision",{'decision':'ACCEPT'})
        candidate=state['revision_candidate']
        assert current(state)['suggestions'][1]['decision'] is None
        state=post(client,state,f"/suggestions/{second['id']}/decision",{'decision':'ACCEPT','confirm_append':True,
            'expected_version_id':candidate['expected_version_id']})
        assert '学生能说明分数含义' in current(state)['current_content']
        assert '补充练习活动' in current(state)['current_content']


def test_context_failure_recorded_without_model_call(environment):
    sessions,factory=environment
    factory.settings.max_context_tokens=256
    with TestClient(create_app(sessions,factory)) as client:
        state,_=create(client,'完整教案')
        post(client,state,'/suggestions',target(state),expected=502)
        export=client.get('/api/sessions/'+state['session']['id']+'/export').json()
        assert export['failures'][0]['error_code']=='CONTEXT_BUDGET'
        assert export['context_snapshots']==[]


def test_development_identity_ignores_client_headers(client):
    owner=client.get('/api/me').json()['id']
    assert client.get('/api/me',headers={'X-User-ID':str(uuid4()),'X-Username':'admin'}).json()['id']==owner


def test_legacy_snapshot_continues_after_alpha_defaults_change(environment):
    sessions,factory=environment
    with TestClient(create_app(sessions,factory)) as client:
        state,_=create(client,'教学目标\n理解分数')
        with sessions.begin() as db:
            session=db.get(m.Session,state['session']['id'])
            session.config_snapshot={**session.config_snapshot,'version':1}
        factory.settings.context_contributors=['unknown_new_plugin']
        state=finish_round(client,state)
        state=post(client,state,'/terminate')
        assert state['session']['status']=='TERMINATED'
        assert len(state['lesson_plan_versions'])==2
