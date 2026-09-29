from io import StringIO
from alembic.config import Config
from alembic import command
from sqlalchemy import inspect
from app.db import make_engine
from app.settings import settings
from pathlib import Path


def test_sqlite_migration_upgrade_and_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'database_url', 'sqlite:///' + str(tmp_path / 'migration.db'))
    cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    command.upgrade(cfg, 'head')
    command.upgrade(cfg, 'head')
    engine = make_engine(settings.database_url)
    assert {'sessions', 'decisions', 'generation_records', 'interaction_events'} <= set(inspect(engine).get_table_names())
    assert len(inspect(engine).get_check_constraints('sessions')) == 1
    assert len(inspect(engine).get_check_constraints('rounds')) == 2
    assert len(inspect(engine).get_check_constraints('decisions')) == 1
    command.check(cfg)
    engine.dispose()


def test_postgresql_offline_migration_uses_jsonb(monkeypatch):
    monkeypatch.setattr(settings, 'database_url', 'postgresql+psycopg://test:test@localhost/test')
    output = StringIO()
    cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'), output_buffer=output)
    command.upgrade(cfg, 'head', sql=True)
    sql = output.getvalue()
    assert 'JSONB' in sql
    assert 'TIMESTAMP WITH TIME ZONE' in sql
    assert 'CREATE TABLE section_versions' in sql
