"""Binary stdin/stdout archive helper for the private documents volume."""
import argparse
import shutil
import sys
import tarfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.settings import settings
from app.storage import LocalDocumentStorage


def pack(root, output):
    store = LocalDocumentStorage(root)
    store.root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=output, mode='w|gz') as archive:
        for path in sorted(store.root.rglob('*')):
            if path.is_symlink(): raise ValueError('Symlinks are not permitted in document storage')
            if path.is_file(): archive.add(path, arcname=path.relative_to(store.root).as_posix(), recursive=False)


def unpack(root, source):
    store = LocalDocumentStorage(root)
    store.root.mkdir(parents=True, exist_ok=True)
    if any(store.root.iterdir()): raise ValueError('Document volume must be empty; existing files are never overwritten')
    with tarfile.open(fileobj=source, mode='r|gz') as archive:
        for member in archive:
            if not member.isfile(): raise ValueError('Archive must contain regular files only')
            target = store.resolve(member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as input_file, target.open('xb') as output_file:
                shutil.copyfileobj(input_file, output_file)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['pack', 'unpack', 'check-empty'])
    args = parser.parse_args()
    if args.action == 'pack': pack(settings.storage_root, sys.stdout.buffer)
    elif args.action == 'unpack': unpack(settings.storage_root, sys.stdin.buffer)
    elif settings.storage_root.exists() and any(settings.storage_root.iterdir()):
        raise SystemExit('Document volume must be empty')
