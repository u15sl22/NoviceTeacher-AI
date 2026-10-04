"""Private attachment storage. Persist relative keys, never machine-specific paths."""
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4


class DocumentStorage(Protocol):
    def put(self, content: bytes, suffix: str) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalDocumentStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve(self, key):
        part = PurePosixPath(key)
        if not key or '\\' in key or ':' in key or part.is_absolute() or '..' in part.parts:
            raise ValueError('Invalid storage key')
        path = (self.root / part).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError('Storage key escapes document root')
        return path

    def put(self, content, suffix):
        if suffix not in ('.docx', '.pdf'):
            raise ValueError('Unsupported document extension')
        key = f'{uuid4().hex}{suffix}'
        path = self.resolve(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
        return key

    def read(self, key):
        return self.resolve(key).read_bytes()

    def delete(self, key):
        self.resolve(key).unlink(missing_ok=True)
