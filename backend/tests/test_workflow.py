from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.api import create_app


def create(client, content='教学目标\n理解分数。\n\n课堂小结\n回顾概念。'):
    payload = {'request_key': str(uuid4()), 'metadata': {'subject': '数学', 'grade': '三年级', 'topic': '分数'}, 'content': content}
    response = client.post('/api/sessions', json=payload)
    assert response.status_code == 200, response.text
    return response.json(), payload


def current(state):
    return next(s for s in state['sections'] if s['id'] == state['session']['current_section_id'])


def target(state):
    return {'round_id': state['round']['id'], 'section_id': current(state)['id']}


def post(client, state, suffix, payload=None, expected=200):
    response = client.post(f"/api/sessions/{state['session']['id']}{suffix}", json=payload or {})
    assert response.status_code == expected, response.text
    return response.json()


def finish_round(client, state, decisions=('ACCEPT', 'REJECT')):
    while state['session']['status'] == 'ACTIVE':
        state = post(client, state, '/suggestions', target(state))
        for i, s in enumerate(current(state)['suggestions']):
            state = post(client, state, f"/suggestions/{s['id']}/decision", {'decision': decisions[i % len(decisions)]})
        state = post(client, state, '/complete-section', target(state))
    return state


def test_full_workflow_history_recovery_and_five_round_limit(client, environment):
    state, payload = create(client)
    sid = state['session']['id']
    assert state['lesson_plan']['original_content'] == payload['content']
    assert len(state['sections']) == 2
    assert state['lesson_plan_versions'][0]['content'] == payload['content']
    state = finish_round(client, state)
    assert state['session']['status'] == 'ROUND_COMPLETED'
    assert len(state['lesson_plan_versions']) == 2
    assert state['lesson_plan']['current_content'] != payload['content']
    for number in range(2, 6):
        old_round = state['round']['id']
        state = post(client, state, f'/rounds/{old_round}/continue')
        assert state['round']['round_number'] == number
        assert post(client, state, f'/rounds/{old_round}/continue')['round']['id'] == state['round']['id']
        state = finish_round(client, state)
    post(client, state, f"/rounds/{state['round']['id']}/continue", expected=409)
    state = post(client, state, '/terminate')
    assert state['session']['terminated_at']
    assert len(state['lesson_plan_versions']) == 6
    sessions, factory = environment
    with TestClient(create_app(sessions, factory)) as restarted:
        restored = restarted.get(f'/api/sessions/{sid}/current-state').json()
        assert restored == state
        assert restarted.get(f'/api/sessions/{sid}/final').json() == state
    export = client.get(f'/api/sessions/{sid}/export').json()
    events = export['interaction_events']
    assert [e['sequence'] for e in events] == list(range(1, len(events) + 1))
    kinds = {e['event_type'] for e in events}
    assert {'SESSION_CREATED', 'LESSON_PLAN_SUBMITTED', 'SECTIONS_PARSED', 'SUGGESTION_GENERATED',
            'SUGGESTION_ACCEPTED', 'SUGGESTION_REJECTED', 'SECTION_UPDATED', 'SECTION_COMPLETED',
            'ROUND_COMPLETED', 'ROUND_CONTINUED', 'SESSION_TERMINATED'} <= kinds
    assert len(export['decisions']) == 4  # Later rounds suppress already accepted/rejected mock suggestions.
    assert len(export['section_versions']) == 4
    assert any(not r['raw_response']['suggestions'] for r in export['generation_records'])
    # Replay the accepted version references to reconstruct the final lesson.
    versions = {v['id']: v for v in export['section_versions']}
    replay = {v['section_id']: v['content'] for v in versions.values() if v['version_number'] == 0}
    for event in events:
        if event['event_type'] == 'SECTION_UPDATED':
            replay[event['section_id']] = versions[event['event_payload']['version_id']]['content']
    assert ''.join(replay[s['id']] for s in state['sections']) == state['lesson_plan']['current_content']


@pytest.mark.parametrize('decisions,version_count', [(('ACCEPT', 'ACCEPT'), 3), (('REJECT', 'REJECT'), 1)])
def test_both_decisions_and_version_chain(client, decisions, version_count):
    state, payload = create(client, '无标题的完整教学内容。')
    state = finish_round(client, state, decisions)
    data = client.get(f"/api/sessions/{state['session']['id']}/export").json()
    versions = sorted(data['section_versions'], key=lambda x: x['version_number'])
    assert len(versions) == version_count
    for previous, version in zip(versions, versions[1:]):
        assert version['previous_version_id'] == previous['id']
        assert previous['content'] in version['content']
    if decisions[0] == 'ACCEPT':
        assert all(s['revision'] in state['lesson_plan']['current_content'] for s in data['suggestions'])
    else:
        assert state['lesson_plan']['current_content'] == payload['content']


def test_duplicate_stale_and_terminated_requests(client):
    state, payload = create(client)
    assert client.post('/api/sessions', json=payload).json()['session']['id'] == state['session']['id']
    assert client.post('/api/sessions', json={**payload, 'content': 'changed'}).status_code == 409
    post(client, state, '/terminate', expected=409)
    post(client, state, f"/rounds/{state['round']['id']}/continue", expected=409)
    t = target(state)
    post(client, state, '/complete-section', t, expected=409)
    state = post(client, state, '/suggestions', t)
    repeated = post(client, state, '/suggestions', t)
    assert repeated == state
    post(client, state, '/complete-section', t, expected=409)
    suggestions = current(state)['suggestions']
    for s in suggestions:
        state = post(client, state, f"/suggestions/{s['id']}/decision", {'decision': 'ACCEPT'})
        assert post(client, state, f"/suggestions/{s['id']}/decision", {'decision': 'ACCEPT'}) == state
        post(client, state, f"/suggestions/{s['id']}/decision", {'decision': 'REJECT'}, expected=409)
    state = post(client, state, '/complete-section', t)
    assert post(client, state, '/complete-section', t) == state
    post(client, state, '/suggestions', t, expected=409)
    state = finish_round(client, state)
    state = post(client, state, '/terminate')
    assert post(client, state, '/terminate') == state
    post(client, state, f"/suggestions/{suggestions[0]['id']}/decision", {'decision': 'ACCEPT'}, expected=409)
    post(client, state, '/complete-section', t, expected=409)


def test_validation_ownership_and_view_log(client):
    state, _ = create(client)
    other, _ = create(client)
    post(client, state, '/suggestions', target(other), expected=409)
    post(client, state, '/view', {**target(other), 'section_version_id': current(other)['version']['id']}, expected=404)
    post(client, state, '/view', {**target(state), 'section_version_id': current(state)['version']['id']})
    state = post(client, state, '/suggestions', target(state))
    post(client, other, f"/suggestions/{current(state)['suggestions'][0]['id']}/decision", {'decision': 'ACCEPT'}, expected=404)
    assert client.get(f"/api/sessions/{state['session']['id']}/final").status_code == 409
    assert client.get(f'/api/sessions/{uuid4()}/current-state').status_code == 404
    export = client.get(f"/api/sessions/{state['session']['id']}/export").json()
    assert 'SECTION_VIEWED' in [e['event_type'] for e in export['interaction_events']]
    for content in ('', ' \n\t'):
        assert client.post('/api/sessions', json={'request_key': str(uuid4()), 'metadata': {
            'subject': '数学', 'grade': '三', 'topic': '分数'}, 'content': content}).status_code == 422


def test_concurrent_generation_and_decision(client):
    state, _ = create(client, '教学目标\n理解分数')
    sid = state['session']['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(f'/api/sessions/{sid}/suggestions', json=target(state)), range(2)))
    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    generated = results[0].json()
    suggestion = current(generated)['suggestions'][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(f"/api/sessions/{sid}/suggestions/{suggestion['id']}/decision",
            json={'decision': 'ACCEPT'}), range(2)))
    assert all(r.status_code == 200 for r in results)
    export = client.get(f'/api/sessions/{sid}/export').json()
    assert len(export['generation_records']) == 1
    assert len(export['decisions']) == 1
    assert len(export['section_versions']) == 2


def test_view_ack_records_rendered_version_instead_of_newer_database_version(client):
    state, _ = create(client, '教学目标\n理解分数')
    old_version = current(state)['version']['id']
    state = post(client, state, '/suggestions', target(state))
    suggestion_ids = [s['id'] for s in current(state)['suggestions']]
    state = post(client, state, f'/suggestions/{suggestion_ids[0]}/decision', {'decision': 'ACCEPT'})
    assert current(state)['version']['id'] != old_version
    post(client, state, '/view', {**target(state), 'section_version_id': old_version,
                                 'suggestion_ids': suggestion_ids, 'decision_ids': []})
    export = client.get(f"/api/sessions/{state['session']['id']}/export").json()
    assert export['interaction_events'][-1]['event_payload']['section_version_id'] == old_version


def test_custom_suggestion_can_repeat_until_terminal_decision(client):
    state, original = create(client, '教学目标\n理解分数。')
    state = post(client, state, '/suggestions', target(state))
    first, second = current(state)['suggestions']

    state = post(client, state, f"/suggestions/{first['id']}/custom-prompt", {
        'request_key': str(uuid4()), 'prompt_text': '请缩短为五分钟，并减少教师讲解。'})
    revised = current(state)['suggestions'][0]
    assert revised['attempt_number'] == 2
    assert len(revised['history']) == 2
    assert revised['history'][0]['decision']['decision'] == 'CUSTOM'
    assert revised['history'][0]['custom_prompt']['prompt_text'].startswith('请缩短')
    post(client, state, '/complete-section', target(state), expected=409)

    state = post(client, state, f"/suggestions/{revised['id']}/custom-prompt", {
        'request_key': str(uuid4()), 'prompt_text': '再改为个人书面活动，不要小组讨论。'})
    final_candidate = current(state)['suggestions'][0]
    assert final_candidate['attempt_number'] == 3
    assert len(final_candidate['history']) == 3
    assert current(state)['current_content'] == original['content']

    state = post(client, state, f"/suggestions/{final_candidate['id']}/decision", {'decision': 'ACCEPT'})
    state = post(client, state, f"/suggestions/{second['id']}/decision", {'decision': 'REJECT'})
    state = post(client, state, '/complete-section', target(state))
    assert state['session']['status'] == 'ROUND_COMPLETED'
    export = client.get(f"/api/sessions/{state['session']['id']}/export").json()
    assert [item['decision'] for item in export['decisions']] == ['CUSTOM', 'CUSTOM', 'ACCEPT', 'REJECT']
    assert len(export['custom_prompts']) == 2
    assert len(export['generation_records']) == 3
    assert {item['generation_kind'] for item in export['generation_records']} == {'INITIAL', 'REFINEMENT'}
