"""Explicit local ownership assignment; retained Participant IDs and history are untouched."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import select, update
from app import models as m
from app.db import SessionLocal
from app.auth import DevelopmentAuthProvider

parser = argparse.ArgumentParser()
parser.add_argument('--username', required=True)
args = parser.parse_args()
with SessionLocal.begin() as db:
    legacy = db.scalar(select(m.User).where(m.User.username == 'legacy-import'))
    if not legacy: raise SystemExit('No legacy owner found.')
    target = DevelopmentAuthProvider(args.username).get_current_user(db)
    count = 0
    for table in m.Base.metadata.sorted_tables:
        if 'user_id' in table.c:
            count += db.execute(update(table).where(table.c.user_id == legacy.id).values(user_id=target.id)).rowcount
    print('Assigned legacy-owned records:', count)
