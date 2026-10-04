import importlib.util
import io
import sys
import tarfile
from pathlib import Path
import pytest
import yaml
from sqlalchemy import create_engine, text
from app.models import Base
from app.settings import Settings
from app.storage import LocalDocumentStorage
from test_workflow import create, finish_round

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_storage_portable_keys_and_archive(tmp_path):
    storage = LocalDocumentStorage(tmp_path / 'original')
    key = storage.put(b'original document bytes', '.docx')
    assert '/' not in key and storage.read(key) == b'original document bytes'
    archive = script('document_archive')
    buffer = io.BytesIO()
    archive.pack(storage.root, buffer)
    buffer.seek(0)
    restored = LocalDocumentStorage(tmp_path / 'restored')
    archive.unpack(restored.root, buffer)
    assert restored.read(key) == storage.read(key)
    with pytest.raises(ValueError, match='empty'):
        archive.unpack(restored.root, io.BytesIO(buffer.getvalue()))


@pytest.mark.parametrize('key', ['../outside.pdf', '/tmp/out.pdf', 'C:/out.pdf', '..\\out.pdf', ''])
def test_storage_rejects_escaping_paths(tmp_path, key):
    with pytest.raises(ValueError): LocalDocumentStorage(tmp_path).resolve(key)


def test_restore_rejects_archive_traversal(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        member = tarfile.TarInfo('../escape.pdf'); member.size = 1
        archive.addfile(member, io.BytesIO(b'x'))
    buffer.seek(0)
    with pytest.raises(ValueError): script('document_archive').unpack(tmp_path / 'docs', buffer)
    assert not (tmp_path / 'escape.pdf').exists()


def test_compose_uses_private_database_and_persistent_volumes():
    config = yaml.safe_load((ROOT / 'compose.yaml').read_text(encoding='utf-8'))
    assert 'ports' not in config['services']['db']
    assert config['services']['app']['environment']['DATABASE_HOST'] == 'db'
    assert config['services']['app']['depends_on']['migrate']['condition'] == 'service_completed_successfully'
    assert config['services']['app']['volumes'] == ['documents:/data/documents']


def test_database_configuration_escapes_password():
    from sqlalchemy.engine import make_url
    cfg = Settings(_env_file=None, database_host='db', database_password='p@ss:/?#')
    assert make_url(cfg.database_url).password == 'p@ss:/?#'
    assert make_url(cfg.database_url).host == 'db'


def test_data_copy_preserves_history_and_refuses_overwrite(client, environment, tmp_path):
    state, _ = create(client)
    finish_round(client, state)
    sessions, _ = environment
    source = sessions.kw['bind']
    if source.dialect.name != 'sqlite':
        pytest.skip('SQLite source-copy unit test; run in the default SQLite suite.')
    destination = create_engine('sqlite:///' + str(tmp_path / 'copy.db'))
    Base.metadata.create_all(destination)
    for engine in [source, destination]:
        with engine.begin() as db:
            db.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            db.execute(text("INSERT INTO alembic_version VALUES ('test-head')"))
    try:
        module = script('migrate_sqlite')
        counts = module.copy_database(source, destination)
        assert counts['sessions'] == 1
        assert counts['decisions'] > 0
        assert counts['context_snapshots'] > 0
        with pytest.raises(ValueError, match='not empty'):
            module.copy_database(source, destination)
    finally:
        destination.dispose()
