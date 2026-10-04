"""Create a consistent, portable SQLite snapshot; never overwrite a destination."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('pedago_loop.db'))
    parser.add_argument('--output', type=Path, default=Path('imports/legacy.db'))
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb'): pass
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as old, closing(sqlite3.connect(output)) as new:
        old.backup(new)
    print('SQLite snapshot saved:', output)
