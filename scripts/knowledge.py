"""Local explicit knowledge ingestion / human verification, independent of the case library."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import select
from app import models as m
from app.auth import DevelopmentAuthProvider
from app.db import SessionLocal

parser=argparse.ArgumentParser()
sub=parser.add_subparsers(dest='command',required=True)
load=sub.add_parser('import'); load.add_argument('file')
verify=sub.add_parser('verify'); verify.add_argument('id'); verify.add_argument('--reviewer',required=True)
verify.add_argument('--notes',required=True); verify.add_argument('--status',choices=['verified','rejected'],required=True)
args=parser.parse_args()
with SessionLocal.begin() as db:
    if args.command=='import':
        items=json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
        count=0
        for raw in items:
            for key in ['content','source','source_type','source_locator','subject','topic']:
                if not isinstance(raw.get(key),str) or not raw[key].strip(): raise ValueError('Missing field: '+key)
            exists=db.scalar(select(m.KnowledgeItem.id).where(m.KnowledgeItem.content==raw['content'],
                m.KnowledgeItem.source==raw['source'],m.KnowledgeItem.source_locator==raw['source_locator']))
            if exists: continue
            db.add(m.KnowledgeItem(**{k:raw[k] for k in ['content','source','source_type','source_locator','subject','topic']},
                grade=raw.get('grade'),section_type=raw.get('section_type'),verification_status='raw',asset_metadata={'raw_payload':raw}))
            count+=1
        print('Raw knowledge imported:',count)
    else:
        item=db.get(m.KnowledgeItem,args.id)
        if not item: raise ValueError('Unknown knowledge id')
        if not args.notes.strip(): raise ValueError('Verification notes required')
        reviewer=DevelopmentAuthProvider(args.reviewer).get_current_user(db)
        item.asset_metadata={**item.asset_metadata,'verification_history':item.asset_metadata.get('verification_history',[])+[
            {'reviewer_user_id':reviewer.id,'notes':args.notes,'status':args.status,'created_at':m.now().isoformat()}]}
        item.verification_status=args.status
        print('Knowledge verification recorded.')
