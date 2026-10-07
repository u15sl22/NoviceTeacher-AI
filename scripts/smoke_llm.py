"""Real-model vertical slice on disposable SQLite or PostgreSQL storage."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema
from app.settings import settings
from app.models import Base
from app.db import make_engine
from app.api import create_app
from app.providers.config import ExperimentConfigFactory


def run(sessions, factory, profile_id):
    with TestClient(create_app(sessions, factory)) as client:
        response = client.post('/api/sessions', json={'request_key': str(uuid4()),
            'metadata': {'subject':'数学','grade':'三年级','topic':'分数的初步认识'},
            # Intentionally vague so the probe validates an actual revision rather than only connectivity.
            'content':'教学目标\n理解分数。',
            'model_profile': profile_id})
        response.raise_for_status(); state = response.json()
        path = '/api/sessions/' + state['session']['id']
        target = {'round_id':state['round']['id'], 'section_id':state['session']['current_section_id']}
        response = client.post(path + '/suggestions', json=target)
        if response.status_code != 200:
            print('FAIL:', response.json()); raise SystemExit(1)
        state = response.json()
        generated = state['sections'][0]['suggestions']
        if not generated:
            raise AssertionError('The real model returned zero suggestions; no revision was validated.')
        for index, suggestion in enumerate(generated):
            response = client.post(path + '/suggestions/' + suggestion['id'] + '/decision', json={
                'decision':'ACCEPT' if index == 0 else 'REJECT'})
            response.raise_for_status()
            if response.json().get('revision_candidate'):
                response = client.post(path + '/suggestions/' + suggestion['id'] + '/decision',
                    json={'decision':'REJECT'})
                response.raise_for_status()
        client.post(path + '/complete-section', json=target).raise_for_status()
        client.post(path + '/terminate').raise_for_status()
        exported = client.get(path + '/export').json()
        assert exported['state']['session']['status'] == 'TERMINATED'
        assert len(exported['generation_records']) == 1
        assert len(exported['context_snapshots']) == 1
        assert exported['context_snapshots'][0]['model_name'] == exported['state']['session']['config_snapshot']['model']
        return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', help='Model profile ID; defaults to DEFAULT_LLM_PROFILE.')
    parser.add_argument('--postgres', action='store_true', help='Use a disposable schema on configured PostgreSQL.')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    factory = ExperimentConfigFactory(settings)
    profile_id = args.profile or settings.default_profile_id()
    profile = next((item for item in factory.public_profiles() if item['id'] == profile_id), None)
    if not profile:
        raise SystemExit('NOT RUN: Unknown model profile: ' + profile_id)
    if not profile['configured']:
        raise SystemExit('NOT RUN: No server-side API key configured for profile: ' + profile_id)

    base_engine = None
    schema = None
    temporary = None
    if args.postgres:
        if not settings.database_url.startswith('postgresql'):
            raise SystemExit('--postgres requires configured PostgreSQL.')
        schema = 'pedago_llm_smoke_' + uuid4().hex
        base_engine = make_engine(settings.database_url)
        with base_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = base_engine.execution_options(schema_translate_map={None: schema})
    else:
        temporary = tempfile.TemporaryDirectory()
        engine = make_engine('sqlite:///' + str(Path(temporary.name) / 'smoke.db'))
    Base.metadata.create_all(engine)
    try:
        exported = run(sessionmaker(engine, expire_on_commit=False), factory, profile_id)
        output = args.output or (None if args.postgres else Path('.runtime/real-alpha-smoke.json'))
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PASS: real model -> decisions -> completed round -> terminated -> export.')
        print('Profile:', profile_id, 'Model:', profile['model'])
        print('Validated suggestions:', len(exported['suggestions']),
            'Context snapshots:', len(exported['context_snapshots']))
    finally:
        engine.dispose()
        if base_engine and schema:
            with base_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            base_engine.dispose()
        if temporary:
            temporary.cleanup()


if __name__ == '__main__':
    main()
