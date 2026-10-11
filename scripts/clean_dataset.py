"""Create derived cleaning artifacts without changing source files or the app database."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.dataset_cleaning import clean_dataset, directory_manifest, file_sha256, write_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lessons', type=Path, default=ROOT / 'docs/测试教案评估/教案汇总.json')
    parser.add_argument('--annotations', type=Path, default=ROOT / 'docs/测试教案评估/批注明细.json')
    parser.add_argument('--source-root', type=Path, default=ROOT / '测试教案/测试教案/原教案')
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.runtime/dataset-cleaning')
    parser.add_argument('--derived-root', type=Path, default=ROOT / '.runtime/dataset-cleaning/derived')
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/dataset-cleaning-report.md')
    args = parser.parse_args()
    inputs = [args.lessons.resolve(), args.annotations.resolve()]
    before = {str(path): {'sha256': file_sha256(path)} for path in inputs}
    before[str(args.source_root.resolve())] = directory_manifest(args.source_root.resolve())
    lessons = json.loads(args.lessons.read_text(encoding='utf-8-sig'))
    annotations = json.loads(args.annotations.read_text(encoding='utf-8-sig'))
    result = clean_dataset(lessons, annotations, args.source_root.resolve(), args.derived_root.resolve())
    after = {str(path): {'sha256': file_sha256(path)} for path in inputs}
    after[str(args.source_root.resolve())] = directory_manifest(args.source_root.resolve())
    if before != after:
        raise RuntimeError('Source JSON changed during cleaning; outputs were not trusted.')
    manifest = write_outputs(result, args.output_dir.resolve(), before, args.report.resolve(),
        directory_manifest(args.derived_root.resolve()))
    print(json.dumps({'status':'ok', 'lessons':manifest['lesson_count'],
        'annotations':manifest['annotation_count'], 'inputs_unchanged':True,
        'database_touched':False, 'external_model_calls':0,
        'output_dir':str(args.output_dir.resolve()), 'report':str(args.report.resolve())},
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
