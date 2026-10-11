import json
from pathlib import Path

from app.dataset_cleaning import (_match_quote, clean_dataset, directory_manifest, file_sha256,
                                  normalize_text, write_outputs)


def test_read_only_cleaning_preserves_sources_and_labels_candidates(tmp_path):
    lessons_path = tmp_path / 'lessons.json'
    annotations_path = tmp_path / 'annotations.json'
    source_root = tmp_path / 'sources'; source_root.mkdir()
    lessons = [{'id':'lesson-1','source_file':'missing.doc','学科':'数学','年级':'小学3年级上册','课题名称':'分数'}]
    annotations = [
        {'lesson':'lesson-1','subject':'数学','dimension':'教学目标','位置':'目标','原文引用':'理解分数',
         '评价':'部分达标','具体分析':'目标不够具体。','建议':'增加可观察行为。','细化建议':'学生能够解释分数。'},
        {'lesson':'lesson-1','subject':'数学','dimension':'教学目标','位置':'目标','原文引用':'理解分数',
         '评价':'部分达标','具体分析':' 目标不够具体。 ','建议':'增加可观察行为。','细化建议':'学生能够解释分数。'},
    ]
    lessons_path.write_text(json.dumps(lessons, ensure_ascii=False), encoding='utf-8')
    annotations_path.write_text(json.dumps(annotations, ensure_ascii=False), encoding='utf-8')
    before = {path: file_sha256(path) for path in (lessons_path, annotations_path)}
    result = clean_dataset(lessons, annotations, source_root)
    manifest = write_outputs(result, tmp_path / 'output',
        {str(path): {'sha256': digest} for path, digest in before.items()})
    assert before == {path: file_sha256(path) for path in before}
    assert manifest['application_database_touched'] is False
    assert manifest['external_model_calls'] == 0
    assert result['records'][0]['grade_candidate'] == '三年级上册'
    assert result['records'][0]['normalized_duplicate_group']
    assert result['records'][0]['verification_status'] == 'not_verified'
    assert result['records'][0]['quote_match_status'] == 'not_available'
    assert directory_manifest(source_root)['file_count'] == 0


def test_normalization_is_conservative():
    assert normalize_text(' 目标　不够具体。\r\n') == '目标 不够具体。'
    assert _match_quote('这是前半部分文本……这是后半部分文本',
                        '这是前半部分文本，中间内容很多，这是后半部分文本',
                        'extracted')[0] == 'excerpt_segments_found'
    assert _match_quote('（教案中无学情分析板块）', '正文', 'extracted')[0] == 'absence_claim'


def test_cleaning_uses_derived_ocr_without_changing_original_pdf(tmp_path):
    source_root = tmp_path / 'sources'; source_root.mkdir()
    derived_root = tmp_path / 'derived'
    original = source_root / 'lesson.pdf'
    original.write_bytes(b'%PDF-1.4\nscanned-placeholder')
    ocr_text = derived_root / 'ocr' / 'lesson.txt'
    ocr_text.parent.mkdir(parents=True)
    ocr_text.write_text('教学目标：学生能够解释分数的含义。', encoding='utf-8')
    lessons = [{'id':'lesson-1','source_file':'lesson.pdf','学科':'数学'}]
    annotations = [{'lesson':'lesson-1','subject':'数学','dimension':'教学目标','位置':'教学目标',
                    '原文引用':'学生能够解释分数的含义', '评价':'达标', '具体分析':'表述具体。',
                    '建议':'保留。', '细化建议':'保留。'}]
    before = file_sha256(original)

    result = clean_dataset(lessons, annotations, source_root, derived_root)

    assert file_sha256(original) == before
    assert result['lesson_profiles']['lesson-1']['extraction_status'] == 'extracted_from_ocr'
    derived_source = Path(result['lesson_profiles']['lesson-1']['derived_source'])
    assert derived_source.parent.name == 'ocr' and derived_source.name == 'lesson.txt'
    assert result['records'][0]['quote_match_status'] == 'exact_unique'
