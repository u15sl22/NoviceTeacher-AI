import sys
import os
from uuid import uuid4
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema
from fastapi.testclient import TestClient
from app.models import Base
from app.db import make_engine
from app.api import create_app
from app.providers.config import ExperimentConfigFactory
from app.settings import Settings


@pytest.fixture
def environment(tmp_path):
    postgres_url = os.environ.get('TEST_DATABASE_URL')
    base_engine = None
    if postgres_url:
        if not postgres_url.startswith('postgresql'):
            raise ValueError('TEST_DATABASE_URL must point to a PostgreSQL test instance')
        schema = 'pedago_test_' + uuid4().hex
        base_engine = make_engine(postgres_url)
        with base_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = base_engine.execution_options(schema_translate_map={None: schema})
    else:
        engine = make_engine('sqlite:///' + str(tmp_path / 'test.db'))
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    factory = ExperimentConfigFactory(Settings(_env_file=None, suggestion_provider='mock'))
    yield sessions, factory
    if base_engine:
        with base_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
    engine.dispose()


@pytest.fixture
def client(environment):
    sessions, factory = environment
    with TestClient(create_app(sessions, factory)) as client:
        yield client
