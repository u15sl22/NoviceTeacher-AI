"""Paired PostgreSQL/documents backup or restore. Requires Docker Compose v2."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--env-file', default='.env.compose')
    args = parser.parse_args()
    compose = ['docker', 'compose', '--env-file', args.env_file]
    def run(parts, **kwargs):
        return subprocess.run(compose + parts, cwd=ROOT, check=True, **kwargs)
    def maintenance(command, **kwargs):
        return run(['run', '--rm', '--no-deps', '-T', 'maintenance', 'python', *command], **kwargs)
    folder = args.directory.resolve()
    if args.action == 'restore':
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('format') != 1: raise ValueError('Unsupported backup format')
        for name in ['database.dump', 'documents.tar.gz']:
            if digest(folder / name) != manifest['sha256'][name]: raise ValueError('Checksum failed: ' + name)
    else:
        folder.mkdir(parents=True, exist_ok=False)
    was_running = bool(run(['ps', '--status', 'running', '-q', 'app'], capture_output=True, text=True).stdout.strip())
    run(['stop', 'app'])
    successful = False
    try:
        if args.action == 'backup':
            with (folder / 'database.dump').open('wb') as output:
                run(['exec', '-T', 'db', 'sh', '-c', 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl'], stdout=output)
            with (folder / 'documents.tar.gz').open('wb') as output:
                maintenance(['scripts/document_archive.py', 'pack'], stdout=output)
            manifest = {'format': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'sha256': {n: digest(folder / n) for n in ['database.dump', 'documents.tar.gz']}}
            (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        else:
            # Restore into a fresh database only; never erase an existing deployment.
            check = run(['exec', '-T', 'db', 'sh', '-c',
                'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema=\'public\'"'], capture_output=True, text=True)
            if check.stdout.strip() != '0': raise ValueError('Restore requires a fresh database with no public tables')
            maintenance(['scripts/document_archive.py', 'check-empty'])
            with (folder / 'documents.tar.gz').open('rb') as source:
                maintenance(['scripts/document_archive.py', 'unpack'], stdin=source)
            with (folder / 'database.dump').open('rb') as source:
                run(['exec', '-T', 'db', 'sh', '-c',
                    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-acl --single-transaction --exit-on-error'], stdin=source)
        successful = True
        print('Completed:', args.action, folder)
    finally:
        # Failed restore stays stopped for inspection; never serve a partial restore.
        if was_running and (args.action == 'backup' or successful): run(['start', 'app'])
        if args.action == 'restore' and not successful:
            print('Restore incomplete. App remains stopped; inspect the fresh target before retrying.')


if __name__ == '__main__': main()
