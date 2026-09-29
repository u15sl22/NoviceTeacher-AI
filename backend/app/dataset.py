"""Explicit raw ingestion and human verification. Never infer verification from source text."""
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from . import models as m


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def import_dataset(db, lessons, annotations, source_group):
    imported_items = imported_annotations = 0
    mapping = {}
    for raw in lessons:
        key = digest([source_group, raw])
        item = db.scalar(select(m.DatasetItem).where(m.DatasetItem.import_key == key))
        if not item:
            reference = str(raw.get('source_file') or raw.get('path', ''))
            item = m.DatasetItem(import_key=key, source_document_name=Path(reference).name,
                source_reference=reference, subject=str(raw.get('subject_normalized') or raw.get('学科', '')),
                original_grade_label=str(raw.get('年级', '')), normalized_grade=None,
                topic=str(raw.get('课题名称', '')), file_format=Path(reference).suffix.lower().lstrip('.'),
                raw_metadata=raw, source_group=source_group)
            db.add(item); db.flush(); imported_items += 1
        label = str(raw['id'])
        if label in mapping: raise ValueError('Duplicate lesson id in import batch: ' + label)
        mapping[label] = item
    for index, raw in enumerate(annotations):
        item = mapping.get(str(raw['lesson']))
        if not item: raise ValueError('Unmatched annotation lesson: ' + str(raw['lesson']))
        # Keep source index: duplicate source annotations remain separate original facts.
        key = digest([source_group, index, raw])
        if db.scalar(select(m.DatasetAnnotation.id).where(m.DatasetAnnotation.import_key == key)): continue
        fields = {'source_dimension': 'dimension', 'source_location': '位置', 'quoted_text': '原文引用',
            'evaluation': '评价', 'analysis': '具体分析', 'suggestion': '建议', 'detailed_suggestion': '细化建议',
            'source_basis': '依据来源', 'theoretical_basis': '理论依据', 'teaching_method_reference': '教学法参考',
            'source_label': 'dimension'}
        db.add(m.DatasetAnnotation(import_key=key, dataset_item_id=item.id, raw_payload=raw, verification_status='raw',
                                  **{target: str(raw.get(source) or '') for target, source in fields.items()}))
        imported_annotations += 1
    db.flush()
    return {'new_items': imported_items, 'new_annotations': imported_annotations}


def verify_annotation(db, annotation_id, reviewer, checks, notes, status):
    if status not in ('reviewed', 'verified', 'rejected'): raise ValueError('Invalid verification status')
    fields = ['issue_exists','location_correct','suggestion_actionable','basis_supported','worth_fixing']
    if set(checks) != set(fields) or any(type(v) is not bool for v in checks.values()):
        raise ValueError('All five explicit boolean verification checks are required')
    if status == 'verified' and not all(checks.values()): raise ValueError('Verified requires all checks to pass')
    if not notes.strip(): raise ValueError('Verification notes are required')
    annotation = db.get(m.DatasetAnnotation, annotation_id)
    if not annotation: raise ValueError('Unknown annotation')
    db.add(m.DatasetVerification(annotation_id=annotation.id, reviewer_user_id=reviewer.id, notes=notes, **checks))
    annotation.verification_status = status
    case = db.scalar(select(m.CaseItem).where(m.CaseItem.source_annotation_id == annotation.id))
    if case: case.verification_status = status  # Revocation immediately removes it from production retrieval.
    db.flush()


def promote_annotation(db, annotation_id, section_type=None, issue_type='other'):
    from .providers.interfaces import SuggestionDraft
    SuggestionDraft(issue='check', reason='check', pedagogical_basis='check', revision='check', issue_type=issue_type)
    annotation = db.get(m.DatasetAnnotation, annotation_id)
    if not annotation or annotation.verification_status != 'verified': raise ValueError('Only verified annotations can be promoted')
    verification = db.scalar(select(m.DatasetVerification).where(m.DatasetVerification.annotation_id == annotation.id)
                            .order_by(m.DatasetVerification.created_at.desc(), m.DatasetVerification.id.desc()))
    if not verification or not all(getattr(verification, x) for x in [
        'issue_exists','location_correct','suggestion_actionable','basis_supported','worth_fixing']):
        raise ValueError('A passing human verification record is required')
    case = db.scalar(select(m.CaseItem).where(m.CaseItem.source_annotation_id == annotation.id))
    if case: return case
    item = db.get(m.DatasetItem, annotation.dataset_item_id)
    case = m.CaseItem(source_dataset_item_id=item.id, source_annotation_id=annotation.id,
        subject=item.subject, grade=item.normalized_grade or item.original_grade_label, topic=item.topic,
        section_type=section_type, issue_type=issue_type, original_text=annotation.quoted_text,
        issue=annotation.evaluation, analysis=annotation.analysis,
        suggestion=annotation.detailed_suggestion or annotation.suggestion, verification_status='verified',
        asset_metadata={'verification_id': verification.id, 'source_label': annotation.source_label})
    db.add(case); db.flush()
    return case
