"""Local operator commands; no public verification/admin endpoints."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.db import SessionLocal
from app.auth import DevelopmentAuthProvider
from app.dataset import import_dataset, verify_annotation, promote_annotation

parser = argparse.ArgumentParser()
commands = parser.add_subparsers(dest='command', required=True)
load = commands.add_parser('import')
load.add_argument('--lessons', required=True); load.add_argument('--annotations', required=True)
load.add_argument('--source-group', default='pilot-41-v1')
verify = commands.add_parser('verify')
verify.add_argument('annotation_id'); verify.add_argument('--reviewer', required=True)
check_input = verify.add_mutually_exclusive_group(required=True)
check_input.add_argument('--checks', help='JSON with five boolean checks')
check_input.add_argument('--checks-file', help='UTF-8 JSON file with five boolean checks')
verify.add_argument('--notes', required=True)
verify.add_argument('--status', choices=['reviewed','verified','rejected'], required=True)
promote = commands.add_parser('promote')
promote.add_argument('annotation_id'); promote.add_argument('--section-type'); promote.add_argument('--issue-type', default='other')
args = parser.parse_args()
try:
    with SessionLocal.begin() as db:
        if args.command == 'import':
            print(import_dataset(db, json.loads(Path(args.lessons).read_text(encoding='utf-8-sig')),
                json.loads(Path(args.annotations).read_text(encoding='utf-8-sig')), args.source_group))
        elif args.command == 'verify':
            reviewer = DevelopmentAuthProvider(args.reviewer).get_current_user(db)
            checks = Path(args.checks_file).read_text(encoding='utf-8-sig') if args.checks_file else args.checks
            verify_annotation(db, args.annotation_id, reviewer, json.loads(checks), args.notes, args.status)
            print('Verification recorded.')
        else:
            print('Case:', promote_annotation(db, args.annotation_id, args.section_type, args.issue_type).id)
except ValueError as error:
    parser.exit(2, str(error) + '\n')
