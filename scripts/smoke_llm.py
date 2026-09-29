"""Real alpha vertical slice on a temporary DB. Only a bundled sample leaves the machine."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from app.settings import settings
from app.models import Base
from app.db import make_engine
from app.api import create_app
from app.providers.config import ExperimentConfigFactory

if not settings.llm_api_key:
    print('NOT RUN: Configure LLM_API_KEY in root .env first.')
    raise SystemExit(2)
settings.suggestion_provider = 'generic_llm'
with tempfile.TemporaryDirectory() as directory:
    engine = make_engine('sqlite:///' + str(Path(directory) / 'smoke.db'))
    Base.metadata.create_all(engine)
    try:
        with TestClient(create_app(sessionmaker(engine, expire_on_commit=False), ExperimentConfigFactory(settings))) as client:
            response = client.post('/api/sessions', json={'request_key': str(uuid4()),
                'metadata': {'subject':'数学','grade':'三年级','topic':'分数的初步认识'},
                'content':'新知探究\n学生将圆形纸片对折，涂出其中一份。教师讲解二分之一的分子和分母。'})
            response.raise_for_status(); state = response.json()
            path = '/api/sessions/' + state['session']['id']
            target = {'round_id':state['round']['id'], 'section_id':state['session']['current_section_id']}
            response = client.post(path + '/suggestions', json=target)
            if response.status_code != 200:
                print('FAIL:', response.json()); raise SystemExit(1)
            state = response.json()
            for index, suggestion in enumerate(state['sections'][0]['suggestions']):
                response = client.post(path + '/suggestions/' + suggestion['id'] + '/decision', json={
                    'decision':'ACCEPT' if index == 0 else 'REJECT'})
                response.raise_for_status()
                if response.json().get('revision_candidate'):
                    response = client.post(path + '/suggestions/' + suggestion['id'] + '/decision', json={'decision':'REJECT'})
                    response.raise_for_status()
            client.post(path + '/complete-section', json=target).raise_for_status()
            client.post(path + '/terminate').raise_for_status()
            exported = client.get(path + '/export').json()
            Path('.runtime').mkdir(exist_ok=True)
            import json
            Path('.runtime/real-alpha-smoke.json').write_text(json.dumps(exported, ensure_ascii=False, indent=2),encoding='utf-8')
            print('PASS: real model -> decisions -> completed round -> terminated -> export.')
            print('Validated suggestions:', len(exported['suggestions']), 'Context snapshots:', len(exported['context_snapshots']))
    finally: engine.dispose()
