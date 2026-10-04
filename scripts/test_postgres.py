"""Run tests using disposable PostgreSQL schemas, never the business schema."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.settings import settings
import pytest

if __name__ == '__main__':
    if not settings.database_url.startswith('postgresql'):
        raise SystemExit('This command requires PostgreSQL configuration.')
    os.environ['TEST_DATABASE_URL'] = settings.database_url
    raise SystemExit(pytest.main(['backend/tests', '-q', '-p', 'no:cacheprovider']))
