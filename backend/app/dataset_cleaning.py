"""Read-only dataset profiling and normalization.

This module never writes the source JSON, lesson files, or application database.
All classifications are mechanical candidates and never imply human verification.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .documents import DocumentParseError, parse_document


CLEANING_VERSION = 'clean-v2'
TEXT_FIELDS = {
    'location': '位置',
    'quote': '原文引用',
    'evaluation': '评价',
    'analysis': '具体分析',
    'suggestion': '建议',
    'detailed_suggestion': '细化建议',
    'source_basis': '依据来源',
    'theoretical_basis': '理论依据',
    'method_reference': '教学法参考',
    'rubric_reference': '档位参照',
}
CATEGORY_MAP = {
    '教学内容': 'content', '教学理念': 'pedagogy', '学情分析': 'learner_analysis',
    '教学目标': 'objectives', '教学资源': 'resources', '教学方法': 'methods',
    '过程设计': 'process_design', '课程思政': 'values_education',
    '课堂互动': 'interaction', '评价反馈': 'assessment_feedback',
    '版式观感': 'visual_layout', '文档规范': 'document_quality',
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def directory_manifest(root: Path) -> dict:
    files = []
    if root.exists():
        for path in sorted((item for item in root.rglob('*') if item.is_file()),
                           key=lambda item: item.relative_to(root).as_posix().casefold()):
            files.append((path.relative_to(root).as_posix(), file_sha256(path)))
    return {'file_count': len(files), 'tree_sha256': stable_digest(files)}


def stable_digest(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def normalize_text(value) -> str:
    text = unicodedata.normalize('NFKC', str(value or ''))
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'[\u200b-\u200f\u2060\ufeff]', '', text)
    lines = [re.sub(r'[ \t\u00a0\u3000]+', ' ', line).strip() for line in text.split('\n')]
    return '\n'.join(lines).strip()


def comparison_text(value) -> str:
    return ''.join(char.casefold() for char in normalize_text(value) if char.isalnum())


def grade_candidate(value) -> str | None:
    text = normalize_text(value)
    match = re.search(r'([一二三四五六七八九十]|[1-9])年级', text)
    if not match:
        return None
    digits = {'1':'一','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七','8':'八','9':'九'}
    grade = digits.get(match.group(1), match.group(1)) + '年级'
    if re.search(r'(上册|上学期)', text):
        grade += '上册'
    elif re.search(r'(下册|下学期)', text):
        grade += '下册'
    return grade


def _source_index(root: Path) -> dict[str, list[Path]]:
    result = defaultdict(list)
    if root.exists():
        for path in root.rglob('*'):
            if path.is_file():
                result[path.name.casefold()].append(path.resolve())
    return result


def _resolve_source(lesson: dict, index: dict[str, list[Path]]) -> tuple[str, Path | None, int]:
    expected = Path(str(lesson.get('source_file') or '')).name
    matches = index.get(expected.casefold(), []) if expected else []
    if len(matches) == 1:
        return 'resolved', matches[0], 1
    if len(matches) > 1:
        return 'ambiguous', None, len(matches)
    return 'missing', None, 0


def _derived_path(path: Path, source_root: Path, derived_root: Path | None) -> Path | None:
    if not derived_root:
        return None
    try:
        relative = path.relative_to(source_root)
    except ValueError:
        return None
    if path.suffix.lower() == '.doc':
        return (derived_root / 'docx' / relative).with_suffix('.docx')
    if path.suffix.lower() == '.pdf':
        return (derived_root / 'ocr' / relative).with_suffix('.txt')
    return None


def _extract_source(path: Path | None, source_root: Path, derived_root: Path | None) -> tuple[str, str | None, str | None, Path | None]:
    if path is None:
        return 'not_available', None, None, None
    derived = _derived_path(path, source_root, derived_root)
    if derived and derived.is_file():
        try:
            if path.suffix.lower() == '.doc':
                parsed = parse_document(derived.read_bytes(), derived.name)
                return 'extracted_from_derived_docx', parsed.text, 'wps-docx-plus-docx-xml-v1', derived
            text = derived.read_text(encoding='utf-8-sig').strip()
            if text:
                return 'extracted_from_ocr', text, 'windows-ocr-zh-hans-v1', derived
        except (DocumentParseError, OSError, UnicodeError) as exc:
            return 'derived_parse_error', None, type(exc).__name__, derived
    if path.suffix.lower() == '.doc':
        return 'unsupported_doc', None, None, None
    try:
        parsed = parse_document(path.read_bytes(), path.name)
        return 'extracted', parsed.text, parsed.parser_name, None
    except DocumentParseError as exc:
        if path.suffix.lower() == '.pdf' and '没有可提取的文字' in str(exc):
            return 'textless_pdf', None, 'ocr_required', None
        return 'parse_error', None, str(exc), None
    except OSError as exc:
        return 'parse_error', None, type(exc).__name__, None


def _match_quote(quote: str, body: str | None, extraction_status: str) -> tuple[str, int]:
    normalized_quote = normalize_text(quote)
    if not normalized_quote:
        return 'document_level', 0
    absence_terms = ('教案中无', '全文无', '均无', '未设置', '未出现', '未见', '没有设置', '缺少')
    if len(normalized_quote) <= 80 and any(term in normalized_quote for term in absence_terms):
        return 'absence_claim', 0
    if not extraction_status.startswith('extracted') or body is None:
        return extraction_status, 0
    normalized_body = normalize_text(body)
    count = normalized_body.count(normalized_quote)
    if count == 1:
        return 'exact_unique', 1
    if count > 1:
        return 'exact_multiple', count
    compact_quote, compact_body = comparison_text(quote), comparison_text(body)
    if len(compact_quote) >= 8:
        count = compact_body.count(compact_quote)
        if count == 1:
            return 'format_only_unique', 1
        if count > 1:
            return 'format_only_multiple', count
    if re.search(r'(?:\.{2,}|…+)', normalized_quote):
        segments = [comparison_text(part) for part in re.split(r'(?:\.{2,}|…+)', normalized_quote)]
        segments = [part for part in segments if len(part) >= 6]
        cursor = 0
        if segments:
            for segment in segments:
                cursor = compact_body.find(segment, cursor)
                if cursor < 0:
                    break
                cursor += len(segment)
            else:
                return 'excerpt_segments_found', len(segments)
    return 'not_found', 0


def _ngrams(value: str, size: int = 4) -> set[str]:
    text = comparison_text(value)
    if len(text) < size:
        return {text} if text else set()
    return {text[index:index + size] for index in range(len(text) - size + 1)}


class _UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def _near_duplicate_groups(records: list[dict], threshold: float = 0.82) -> list[list[int]]:
    groups_by_dimension = defaultdict(list)
    for index, record in enumerate(records):
        text = '\n'.join(record['normalized'][key] for key in ('analysis', 'suggestion', 'detailed_suggestion'))
        grams = _ngrams(text)
        if len(grams) >= 8:
            groups_by_dimension[record['dimension_original']].append((index, grams))
    union = _UnionFind(len(records))
    for candidates in groups_by_dimension.values():
        for position, (left_index, left) in enumerate(candidates):
            for right_index, right in candidates[position + 1:]:
                ratio = min(len(left), len(right)) / max(len(left), len(right))
                if ratio < 0.65:
                    continue
                score = len(left & right) / len(left | right)
                if score >= threshold:
                    union.union(left_index, right_index)
    found = defaultdict(list)
    for index in range(len(records)):
        found[union.find(index)].append(index)
    return [items for items in found.values() if len(items) > 1]


def _assign_group(records, indexes, field, prefix, signature=None):
    values = [records[index]['raw_digest'] for index in indexes]
    group_id = prefix + '-' + stable_digest(signature or values)[:12]
    for index in indexes:
        records[index][field] = group_id
    return group_id


def clean_dataset(lessons: list[dict], annotations: list[dict], source_root: Path,
                  derived_root: Path | None = None) -> dict:
    lesson_map = {str(item.get('id')): item for item in lessons}
    if len(lesson_map) != len(lessons):
        raise ValueError('Lesson IDs must be present and unique.')
    source_index = _source_index(source_root)
    lesson_profiles = {}
    lesson_document_hashes = defaultdict(list)
    for lesson_id, lesson in lesson_map.items():
        resolution, path, match_count = _resolve_source(lesson, source_index)
        extraction, body, parser, derived_path = _extract_source(path, source_root, derived_root)
        document_hash = stable_digest(normalize_text(body)) if body else None
        if document_hash:
            lesson_document_hashes[document_hash].append(lesson_id)
        lesson_profiles[lesson_id] = {
            'source_resolution': resolution,
            'source_match_count': match_count,
            'resolved_source': str(path) if path else None,
            'source_format': path.suffix.lower().lstrip('.') if path else Path(str(lesson.get('source_file') or '')).suffix.lower().lstrip('.'),
            'extraction_status': extraction,
            'parser': parser,
            'derived_source': str(derived_path) if derived_path else None,
            'body': body,
            'document_hash': document_hash,
            'subject_original': normalize_text(lesson.get('学科') or lesson.get('subject_normalized')),
            'subject_candidate': normalize_text(lesson.get('subject_normalized') or lesson.get('学科')) or None,
            'grade_original': normalize_text(lesson.get('年级')),
            'grade_candidate': grade_candidate(lesson.get('年级')),
            'topic_original': normalize_text(lesson.get('课题名称')),
        }

    records = []
    for index, raw in enumerate(annotations):
        lesson_id = str(raw.get('lesson') or '')
        lesson = lesson_map.get(lesson_id)
        profile = lesson_profiles.get(lesson_id)
        normalized = {name: normalize_text(raw.get(source)) for name, source in TEXT_FIELDS.items()}
        raw_digest = stable_digest(raw)
        flags = []
        if not lesson:
            flags.append('unknown_lesson')
            profile = {'body': None, 'extraction_status': 'not_available', 'source_resolution': 'missing',
                'source_format': '', 'resolved_source': None, 'grade_candidate': None,
                'subject_candidate': None, 'topic_original': None, 'document_hash': None}
        quote_status, quote_occurrences = _match_quote(normalized['quote'], profile['body'], profile['extraction_status'])
        if quote_status in {'not_found', 'exact_multiple', 'format_only_multiple', 'parse_error', 'not_available'}:
            flags.append('source_location_needs_review')
        if profile['extraction_status'] == 'unsupported_doc':
            flags.append('source_check_not_supported_for_doc')
        required = ['evaluation', 'analysis', 'suggestion']
        flags.extend('missing_' + field for field in required if not normalized[field])
        leakage_fields = []
        body_compact = comparison_text(profile['body']) if profile.get('body') else ''
        for field in ('analysis', 'suggestion', 'detailed_suggestion', 'rubric_reference'):
            candidate = comparison_text(normalized[field])
            if len(candidate) >= 24 and candidate in body_compact:
                leakage_fields.append(field)
        if leakage_fields:
            flags.append('annotation_text_present_in_source')
        changed_fields = [name for name, source in TEXT_FIELDS.items()
                          if normalized[name] != str(raw.get(source) or '')]
        signature_fields = ['quote', 'evaluation', 'analysis', 'suggestion', 'detailed_suggestion']
        normalized_signature = stable_digest([lesson_id] + [comparison_text(normalized[x]) for x in signature_fields])
        cross_lesson_signature = stable_digest([comparison_text(normalized[x]) for x in signature_fields])
        records.append({
            'record_id': f'annotation-{index + 1:04d}', 'source_index': index,
            'raw_digest': raw_digest, 'lesson_id': lesson_id,
            'lesson_found': bool(lesson), 'subject_original': normalize_text(raw.get('subject')),
            'subject_candidate': profile.get('subject_candidate'),
            'grade_candidate': profile.get('grade_candidate'), 'topic_candidate': profile.get('topic_original'),
            'dimension_original': normalize_text(raw.get('dimension')),
            'category_candidate': CATEGORY_MAP.get(normalize_text(raw.get('dimension'))),
            'category_mapping_rule': 'exact_alias_v1' if normalize_text(raw.get('dimension')) in CATEGORY_MAP else None,
            'normalized': normalized, 'changed_fields': changed_fields,
            'source_resolution': profile.get('source_resolution'),
            'source_format': profile.get('source_format'), 'resolved_source': profile.get('resolved_source'),
            'derived_source': profile.get('derived_source'),
            'document_hash': profile.get('document_hash'), 'quote_match_status': quote_status,
            'quote_occurrences': quote_occurrences, 'leakage_candidate_fields': leakage_fields,
            'flags': sorted(set(flags)), 'review_status': 'unreviewed',
            'verification_status': 'not_verified', 'cleaning_version': CLEANING_VERSION,
            '_normalized_signature': normalized_signature,
            '_cross_lesson_signature': cross_lesson_signature,
            'raw_duplicate_group': None, 'normalized_duplicate_group': None,
            'cross_lesson_duplicate_group': None, 'near_duplicate_candidate_group': None,
            'template_candidates': [],
        })

    for field, prefix in [('raw_digest', 'RAW'), ('_normalized_signature', 'NORM'),
                          ('_cross_lesson_signature', 'CROSS')]:
        grouped = defaultdict(list)
        for index, record in enumerate(records):
            grouped[record[field]].append(index)
        output_field = {'raw_digest':'raw_duplicate_group', '_normalized_signature':'normalized_duplicate_group',
                        '_cross_lesson_signature':'cross_lesson_duplicate_group'}[field]
        for signature, indexes in grouped.items():
            if len(indexes) > 1:
                _assign_group(records, indexes, output_field, prefix, signature)

    near_groups = _near_duplicate_groups(records)
    for indexes in near_groups:
        _assign_group(records, indexes, 'near_duplicate_candidate_group', 'NEAR')

    template_occurrences = defaultdict(list)
    for index, record in enumerate(records):
        for field in ('analysis', 'suggestion', 'detailed_suggestion'):
            value = comparison_text(record['normalized'][field])
            if len(value) >= 20:
                template_occurrences[(field, value)].append(index)
    template_groups = []
    for (field, value), indexes in template_occurrences.items():
        lessons_used = {records[index]['lesson_id'] for index in indexes}
        if len(lessons_used) < 3:
            continue
        group_id = 'TPL-' + stable_digest([field, value])[:12]
        template_groups.append({'group_id': group_id, 'field': field, 'count': len(indexes),
                                'lesson_count': len(lessons_used),
                                'text_preview': records[indexes[0]]['normalized'][field][:300],
                                'record_ids': [records[x]['record_id'] for x in indexes]})
        for index in indexes:
            records[index]['template_candidates'].append({'group_id': group_id, 'field': field,
                'count': len(indexes), 'lesson_count': len(lessons_used)})

    duplicate_documents = {digest: ids for digest, ids in lesson_document_hashes.items() if len(ids) > 1}
    for record in records:
        record.pop('_normalized_signature')
        record.pop('_cross_lesson_signature')
    return {'lessons': lessons, 'records': records, 'lesson_profiles': lesson_profiles,
            'near_groups': near_groups, 'template_groups': template_groups,
            'duplicate_documents': duplicate_documents}


def _safe_csv(value):
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value or '')
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


def _write_csv(path: Path, fieldnames: list[str], rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _safe_csv(row.get(field)) for field in fieldnames})


def _counter_markdown(title, values):
    lines = [f'### {title}', '', '| 值 | 数量 |', '|---|---:|']
    lines.extend(f'| {key or "（空）"} | {count} |' for key, count in Counter(values).most_common())
    return '\n'.join(lines)


def write_outputs(result: dict, output_dir: Path, input_manifest: dict, report_path: Path | None = None,
                  derived_manifest: dict | None = None):
    output_dir.mkdir(parents=True, exist_ok=True)
    records = result['records']
    jsonl_path = output_dir / 'cleaned_annotations.jsonl'
    with jsonl_path.open('w', encoding='utf-8', newline='\n') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n')

    duplicate_rows = []
    for record in records:
        for kind in ('raw_duplicate_group', 'normalized_duplicate_group', 'cross_lesson_duplicate_group'):
            if record[kind]:
                duplicate_rows.append({'group_type': kind, 'group_id': record[kind],
                    'record_id': record['record_id'], 'lesson_id': record['lesson_id'],
                    'dimension': record['dimension_original'],
                    'quote': record['normalized']['quote'],
                    'evaluation': record['normalized']['evaluation'],
                    'suggestion': record['normalized']['suggestion']})
    _write_csv(output_dir / 'duplicate_groups.csv',
        ['group_type','group_id','record_id','lesson_id','dimension','quote','evaluation','suggestion'], duplicate_rows)

    source_rows = [{'record_id': r['record_id'], 'lesson_id': r['lesson_id'], 'dimension': r['dimension_original'],
        'location': r['normalized']['location'], 'quote': r['normalized']['quote'],
        'source_resolution': r['source_resolution'], 'source_format': r['source_format'],
        'quote_match_status': r['quote_match_status'], 'quote_occurrences': r['quote_occurrences'],
        'flags': r['flags']} for r in records if r['quote_match_status'] not in {'exact_unique','document_level'}]
    _write_csv(output_dir / 'source_issues.csv', ['record_id','lesson_id','dimension','location','quote','source_resolution',
        'source_format','quote_match_status','quote_occurrences','flags'], source_rows)

    lesson_rows = []
    for lesson in result['lessons']:
        lesson_id = str(lesson.get('id'))
        profile = result['lesson_profiles'][lesson_id]
        lesson_rows.append({'lesson_id':lesson_id, 'subject':profile['subject_candidate'],
            'grade_original':profile['grade_original'], 'grade_candidate':profile['grade_candidate'],
            'topic':profile['topic_original'], 'source_format':profile['source_format'],
            'source_resolution':profile['source_resolution'], 'extraction_status':profile['extraction_status'],
            'parser':profile['parser'], 'resolved_source':profile['resolved_source'],
            'derived_source':profile['derived_source'],
            'document_hash':profile['document_hash']})
    _write_csv(output_dir / 'lesson_profiles.csv', ['lesson_id','subject','grade_original','grade_candidate',
        'topic','source_format','source_resolution','extraction_status','parser','resolved_source','derived_source','document_hash'], lesson_rows)

    template_rows = []
    for item in result['template_groups']:
        template_rows.append({**item, 'record_ids': item['record_ids']})
    _write_csv(output_dir / 'template_candidates.csv',
        ['group_id','field','count','lesson_count','text_preview','record_ids'], template_rows)

    near_rows = []
    for indexes in result['near_groups']:
        group_id = records[indexes[0]]['near_duplicate_candidate_group']
        for index in indexes:
            record = records[index]
            near_rows.append({'group_id': group_id, 'record_id': record['record_id'],
                'lesson_id': record['lesson_id'], 'dimension': record['dimension_original'],
                'analysis':record['normalized']['analysis'], 'suggestion':record['normalized']['suggestion']})
    _write_csv(output_dir / 'near_duplicate_candidates.csv',
        ['group_id','record_id','lesson_id','dimension','analysis','suggestion'], near_rows)

    source_counts = Counter(profile['source_resolution'] for profile in result['lesson_profiles'].values())
    extraction_counts = Counter(profile['extraction_status'] for profile in result['lesson_profiles'].values())
    quote_counts = Counter(record['quote_match_status'] for record in records)
    flag_counts = Counter(flag for record in records for flag in record['flags'])
    group_counts = {
        field: len({record[field] for record in records if record[field]})
        for field in ('raw_duplicate_group','normalized_duplicate_group','cross_lesson_duplicate_group',
                      'near_duplicate_candidate_group')}
    changed_record_count = sum(bool(record['changed_fields']) for record in records)
    mapped_category_count = sum(record['category_candidate'] is not None for record in records)
    leakage_candidate_count = sum(bool(record['leakage_candidate_fields']) for record in records)
    derived_docx_count = extraction_counts.get('extracted_from_derived_docx', 0)
    derived_ocr_count = extraction_counts.get('extracted_from_ocr', 0)
    pending_binary_count = extraction_counts.get('unsupported_doc', 0) + extraction_counts.get('textless_pdf', 0)
    manifest = {
        'cleaning_version': CLEANING_VERSION,
        'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'inputs': input_manifest,
        'inputs_unchanged': True,
        'application_database_touched': False,
        'external_model_calls': 0,
        'derived_artifacts': derived_manifest,
        'lesson_count': len(result['lessons']), 'annotation_count': len(records),
        'output_hashes': {},
    }

    report = f'''# 数据集只读清洗报告

生成时间：{manifest['generated_at']}  
清洗版本：`{CLEANING_VERSION}`

## 边界

- 原始教案和原始 JSON 均以只读方式处理，运行前后 SHA-256 必须一致。
- 未连接或写入应用数据库；未调用任何外部模型。
- 所有记录保持 `unreviewed / not_verified`。相似、模板和泄漏均为待人工检查的候选标记，不表示正确、错误或已核验。

## 总览

| 项目 | 数量 |
|---|---:|
| 教案 | {len(result['lessons'])} |
| 原始批注 | {len(records)} |
| 产生格式标准化派生值的批注 | {changed_record_count} |
| 原始维度可按精确别名映射 | {mapped_category_count} |
| 批注文本疑似已混入原教案 | {leakage_candidate_count} |
| 使用派生 DOCX完成检查的旧 DOC | {derived_docx_count} |
| 使用本地 OCR完成检查的扫描 PDF | {derived_ocr_count} |
| 原始字节完全重复组 | {group_counts['raw_duplicate_group']} |
| 同教案标准化重复组 | {group_counts['normalized_duplicate_group']} |
| 跨教案内容完全相同组 | {group_counts['cross_lesson_duplicate_group']} |
| 疑似近似重复组 | {group_counts['near_duplicate_candidate_group']} |
| 跨至少3份教案的模板候选组 | {len(result['template_groups'])} |
| 疑似重复教案正文组 | {len(result['duplicate_documents'])} |

{_counter_markdown('教案来源定位状态（按教案）', source_counts.elements())}

{_counter_markdown('正文提取状态（按教案）', extraction_counts.elements())}

{_counter_markdown('原文引用定位状态', quote_counts.elements())}

{_counter_markdown('机械检查标记', flag_counts.elements())}

{_counter_markdown('原始维度分布', (r['dimension_original'] for r in records))}

## 详细结果

详细派生记录保存在 `{output_dir.as_posix()}`：

- `cleaned_annotations.jsonl`：逐条标准化结果与完整来源追踪；
- `lesson_profiles.csv`：41份原教案的格式、解析状态和候选标准化元数据；
- `duplicate_groups.csv`：确定性重复组；
- `near_duplicate_candidates.csv`：只供人工复核的相似组；
- `template_candidates.csv`：跨教案重复文本候选；
- `source_issues.csv`：无法唯一定位或暂不支持解析的记录；
- `manifest.json`：输入输出哈希和运行边界。

派生 DOCX和 OCR文本只用于机械定位，不替代原始文件，也不表示内容已经人工核验。仍未取得可检查正文的二进制文件数量为 {pending_binary_count}；OCR识别误差可能使部分引用进入 `not_found`。怎样定义人工评价标准仍需另行决定。
'''
    (output_dir / 'report.md').write_text(report, encoding='utf-8')
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding='utf-8')
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != 'manifest.json':
            manifest['output_hashes'][path.name] = file_sha256(path)
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest

