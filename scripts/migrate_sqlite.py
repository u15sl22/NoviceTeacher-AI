"""Copy an upgraded SQLite database into an empty PostgreSQL schema, atomically."""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import create_engine, select, text, func
from app.models import Base
from app.settings import settings


def canonical(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict): return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list): return [canonical(v) for v in value]
    return value


def copy_database(source, destination):
    counts = {}
    with source.connect() as old, destination.begin() as new:
        old_version = old.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        new_version = new.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        if old_version != new_version: raise ValueError('Schema revisions differ; upgrade a COPY of SQLite first.')
        tables = list(Base.metadata.sorted_tables)
        for table in tables:
            if new.scalar(select(func.count()).select_from(table)):
                raise ValueError('Destination is not empty: ' + table.name)
        for table in tables:
            rows = [dict(row) for row in old.execute(select(table).order_by(table.c.created_at, table.c.id)).mappings()]
            # Preserve all identifiers, JSON, timestamps and links; no new ORM defaults.
            for row in rows: new.execute(table.insert().values(**row))
            copied = [dict(row) for row in new.execute(select(table).order_by(table.c.created_at, table.c.id)).mappings()]
            # Compare by primary key, not database-specific timestamp sorting.
            expected = {r['id']: canonical(r) for r in rows}
            actual = {r['id']: canonical(r) for r in copied}
            if expected != actual: raise ValueError('Verification failed: ' + table.name)
            counts[table.name] = len(rows)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    path = args.source.resolve(strict=True)
    if not settings.database_url.startswith('postgresql'):
        parser.error('Destination DATABASE_URL must be PostgreSQL (or configure DATABASE_HOST).')
    # Docker mounts imports read-only. ``immutable=1`` prevents SQLite from
    # trying to create journal/shared-memory files next to the snapshot.
    source_uri = f'file:{path.as_posix()}?mode=ro&immutable=1'
    source = create_engine('sqlite://', creator=lambda: sqlite3.connect(source_uri, uri=True))
    destination = create_engine(settings.database_url)
    try:
        print(json.dumps(copy_database(source, destination), indent=2))
        print('PASS: all original columns verified; transaction committed.')
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    finally:
        source.dispose(); destination.dispose()


if __name__ == '__main__': main()
