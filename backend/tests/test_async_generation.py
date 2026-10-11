from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from app import models as m
from app.api import create_app
from app.analysis import split_text
from app.providers.interfaces import SuggestionDraft
from app.settings import Settings
from app.providers.config import ExperimentConfigFactory
from app.worker import GenerationWorker


class CountingProvider:
    def __init__(self):
        self.calls = 0
        self.last_raw = None

    def generate(self, context):
        self.calls += 1
        self.last_raw = {'call': self.calls}
        return [SuggestionDraft(issue='目标不够可观察', reason='难以检查学习结果',
            pedagogical_basis='目标应可观察', revision='学生能够解释本节核心概念。',
            target_text=None, revision_mode='append')]


def generic_environment(tmp_path):
    from sqlalchemy.orm import sessionmaker
    from app.db import make_engine
    engine = make_engine('sqlite:///' + str(tmp_path / 'async.db'))
    m.Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, suggestion_provider='generic_llm', llm_api_key='test-key',
        context_contributors=[], max_context_bytes=4096)
    factory = ExperimentConfigFactory(settings)
    provider = CountingProvider()
    original = factory.build
    factory.build = lambda snapshot, history: replace(original(snapshot, history), suggestion_provider=provider)
    return engine, sessions, factory, provider


def payload(content='教学目标\n理解分数。'):
    return {'request_key': str(uuid4()), 'metadata': {'subject': '数学', 'grade': '三年级',
        'topic': '分数'}, 'content': content, 'model_profile': 'deepseek'}


def target(state):
    return {'round_id': state['round']['id'], 'section_id': state['session']['current_section_id']}


def current(state):
    return next(item for item in state['sections'] if item['id'] == state['session']['current_section_id'])


def test_external_provider_runs_only_after_request_commits(tmp_path):
    engine, sessions, factory, provider = generic_environment(tmp_path)
    try:
        with TestClient(create_app(sessions, factory)) as client:
            state = client.post('/api/sessions', json=payload()).json()
            response = client.post(f"/api/sessions/{state['session']['id']}/suggestions", json=target(state))
            assert response.status_code == 202
            assert provider.calls == 0
            assert response.json()['generation_job']['status'] == 'QUEUED'

            assert GenerationWorker(sessions, factory).run_once() is True
            finished = client.get(f"/api/sessions/{state['session']['id']}/current-state").json()
            assert provider.calls == 1
            assert finished['generation_job']['status'] == 'SUCCEEDED'
            assert len(current(finished)['suggestions']) == 1
    finally:
        engine.dispose()


def test_worker_discards_result_if_section_version_changed(tmp_path):
    engine, sessions, factory, provider = generic_environment(tmp_path)
    try:
        with TestClient(create_app(sessions, factory)) as client:
            state = client.post('/api/sessions', json=payload()).json()
            response = client.post(f"/api/sessions/{state['session']['id']}/suggestions", json=target(state))
            assert response.status_code == 202
            section_id = state['session']['current_section_id']
            with sessions.begin() as db:
                section = db.get(m.Section, section_id)
                previous = db.get(m.SectionVersion, current(state)['version']['id'])
                section.current_content += '\n人工并发修改'
                db.add(m.SectionVersion(user_id=section.user_id, section_id=section.id, round_number=1,
                    version_number=previous.version_number + 1, previous_version_id=previous.id,
                    content=section.current_content))

            assert GenerationWorker(sessions, factory).run_once() is True
            finished = client.get(f"/api/sessions/{state['session']['id']}/current-state").json()
            assert provider.calls == 1
            assert finished['generation_job']['status'] == 'STALE'
            assert current(finished)['suggestions'] == []
    finally:
        engine.dispose()


def test_long_section_has_preflight_and_bounded_chunks(tmp_path):
    chunks = split_text('一' * 5000, 1024)
    assert len(chunks) > 1
    assert all(len(chunk.content.encode('utf-8')) <= 1024 for chunk in chunks)
    assert ''.join(chunk.content[chunk.overlap_before:] for chunk in chunks) == '一' * 5000

    engine, sessions, factory, provider = generic_environment(tmp_path)
    try:
        with TestClient(create_app(sessions, factory)) as client:
            request = payload('教学过程\n' + '学生观察、讨论并解释。' * 300)
            plan = client.post('/api/analysis-plan', json={key: request[key]
                for key in ('metadata', 'content', 'model_profile')})
            assert plan.status_code == 200
            assert plan.json()['analysis_chunk_count'] > 1
            assert plan.json()['context_budget_bytes'] == 4096
            state = client.post('/api/sessions', json=request).json()
            queued = client.post(f"/api/sessions/{state['session']['id']}/suggestions", json=target(state))
            assert queued.status_code == 202
            assert GenerationWorker(sessions, factory).run_once() is True
            finished = client.get(f"/api/sessions/{state['session']['id']}/current-state").json()
            expected_calls = plan.json()['sections'][0]['chunk_count']
            assert provider.calls == expected_calls
            assert finished['generation_job']['status'] == 'SUCCEEDED'
            assert len(current(finished)['suggestions']) == 1
    finally:
        engine.dispose()
