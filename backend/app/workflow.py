"""Stable workflow with short request transactions and durable model jobs.

PostgreSQL locks one session row; local SQLite uses BEGIN IMMEDIATE. External
model HTTP is executed by ``app.worker`` after the request transaction commits.
"""
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select, func
from . import models as m
from .history import HistoryReader
from .providers.interfaces import SuggestionDraft
from .context import ContextBuildRequest
from .audit import GenerationAudit
from .auth import AuthorizationPolicy
from .analysis import build_analysis_plan, content_budget, split_text
from .context import SYSTEM_PROMPT, REFINEMENT_SYSTEM_PROMPT, REFINEMENT_PROMPT_VERSION
from .providers.llm import ProviderError


class WorkflowError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def dump(row):
    def normalize(value):
        if isinstance(value, datetime):
            return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc)
        return value
    return {col.key: normalize(getattr(row, col.key)) for col in row.__table__.columns}


def document_dump(row, include_text=False):
    data = {key: getattr(row, key) for key in ('id', 'session_id', 'original_filename',
        'media_type', 'byte_size', 'sha256', 'parse_status', 'parser_name', 'page_count',
        'parse_error', 'created_at', 'updated_at')}
    if include_text:
        data['extracted_text'] = row.extracted_text
    return data


def job_dump(row, generation=None):
    result = {key: getattr(row, key) for key in ('id', 'session_id', 'round_id', 'section_id',
        'section_version_id', 'generation_id', 'status', 'attempt_count', 'error_code',
        'created_at', 'started_at', 'finished_at')}
    if generation:
        result['generation_kind'] = generation.generation_kind
        result['parent_suggestion_id'] = generation.parent_suggestion_id
        result['custom_prompt_id'] = generation.custom_prompt_id
    return result


class Workflow:
    def __init__(self, db, factory, current_user):
        self.db, self.factory, self.current_user = db, factory, current_user

    def add(self, cls, **values):
        if hasattr(cls, 'user_id'): values['user_id'] = self.current_user.id
        row = cls(**values)
        self.db.add(row)
        self.db.flush()
        return row

    def one(self, cls, **where):
        if hasattr(cls, 'user_id'): where['user_id'] = self.current_user.id
        return self.db.scalar(select(cls).filter_by(**where))

    def rows(self, cls, **where):
        if hasattr(cls, 'user_id'): where['user_id'] = self.current_user.id
        return self.db.scalars(select(cls).filter_by(**where).order_by(cls.created_at, cls.id)).all()

    def session(self, session_id):
        row = self.db.scalar(select(m.Session).where(m.Session.id == session_id, m.Session.user_id == self.current_user.id).with_for_update())
        if not row or not AuthorizationPolicy().can_access(self.current_user, row):
            raise WorkflowError('找不到此会话，请检查恢复链接。', 404)
        return row

    def config(self, session):
        return self.factory.build(session.config_snapshot, HistoryReader(self.db, self.current_user.id))

    def analysis_plan(self, data):
        try:
            snapshot = self.factory.snapshot(data.model_profile)
            config = self.factory.build(snapshot, HistoryReader(self.db, self.current_user.id))
            drafts = config.section_parser.parse(data.content, data.metadata.model_dump(mode='json'))
            plan = build_analysis_plan(drafts, snapshot.get('max_context_bytes', snapshot.get('max_context_tokens', 12000)),
                SYSTEM_PROMPT, snapshot.get('max_analysis_chunks_per_section', 24),
                snapshot.get('max_analysis_chunks_per_lesson', 80))
        except ValueError as exc:
            raise WorkflowError(str(exc), 422) from exc
        if not drafts or ''.join(d.content for d in drafts) != data.content:
            raise WorkflowError('切分器必须返回非空列表并完整保留原文。', 422)
        return plan, drafts, snapshot

    def event(self, session, event_type, payload=None, round_id=None, section_id=None):
        sequence = self.db.scalar(select(func.coalesce(func.max(m.InteractionEvent.sequence), 0))
                                  .where(m.InteractionEvent.session_id == session.id, m.InteractionEvent.user_id == self.current_user.id)) + 1
        session.updated_at = m.now()
        return self.add(m.InteractionEvent, participant_id=session.participant_id, session_id=session.id,
            round_id=round_id, section_id=section_id, sequence=sequence,
            event_type=event_type, event_payload=payload or {})

    def sections(self, lesson_id):
        return self.db.scalars(select(m.Section).where(m.Section.lesson_plan_id == lesson_id, m.Section.user_id == self.current_user.id)
                               .order_by(m.Section.order_index)).all()

    def latest(self, section_id):
        return self.db.scalar(select(m.SectionVersion).where(m.SectionVersion.section_id == section_id, m.SectionVersion.user_id == self.current_user.id)
                              .order_by(m.SectionVersion.version_number.desc()).limit(1))

    def create_session(self, data):
        payload = data.model_dump(mode='json')
        fingerprint = hashlib.sha256(json.dumps({'metadata': payload['metadata'], 'content': data.content,
                                                 'document_id': payload.get('document_id'),
                                                 'model_profile': payload.get('model_profile')},
                                                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = self.one(m.Session, request_key=str(data.request_key))
        if existing:
            if existing.input_hash != fingerprint:
                raise WorkflowError('此提交编号已经用于不同教案。请新建提交。')
            return existing
        if not data.content.strip():
            raise WorkflowError('教案不能为空。', 422)
        document = None
        if data.document_id:
            document = self.one(m.UploadedDocument, id=str(data.document_id))
            if not document:
                raise WorkflowError('找不到已上传的源文件，请重新上传。', 404)
            if document.parse_status != 'parsed' or not document.extracted_text:
                raise WorkflowError('源文件仍在识别或识别失败，请先完成 OCR 并校对正文。', 409)
            if document.session_id:
                raise WorkflowError('此源文件已经用于另一份教案，请重新上传。')
        plan, drafts, snapshot = self.analysis_plan(data)
        if not plan['processable']:
            raise WorkflowError('教案超过当前安全分析上限，请缩短正文或增加清晰的教学单元标题。', 422)
        participant = self.add(m.Participant)
        session = self.add(m.Session, participant_id=participant.id, request_key=str(data.request_key),
                           input_hash=fingerprint, lesson_metadata=payload['metadata'], config_snapshot=snapshot)
        if document:
            document.session_id = session.id
        self.add(m.SystemConfigSnapshot, session_id=session.id, version=snapshot['version'], config=snapshot)
        self.add(m.Conversation, session_id=session.id)
        self.event(session, 'SESSION_CREATED', {'config': snapshot})
        lesson = self.add(m.LessonPlan, session_id=session.id, original_content=data.content, current_content=data.content)
        original = self.add(m.LessonPlanVersion, lesson_plan_id=lesson.id, round_number=0, content=data.content)
        self.event(session, 'LESSON_PLAN_SUBMITTED', {'lesson_plan_id': lesson.id, 'version_id': original.id})
        if document:
            self.event(session, 'SOURCE_DOCUMENT_ATTACHED', {'document_id': document.id,
                'filename': document.original_filename, 'sha256': document.sha256,
                'parser': document.parser_name})
        self.chat(session, 'user', data.content, 'lesson_plan')
        sections = []
        for i, draft in enumerate(drafts):
            section = self.add(m.Section, lesson_plan_id=lesson.id, title=draft.title,
                               order_index=i, section_type=draft.section_type, current_content=draft.content)
            self.add(m.SectionVersion, section_id=section.id, round_number=0, version_number=0, content=draft.content)
            sections.append(section)
        self.event(session, 'SECTIONS_PARSED', {'parser': snapshot['section_parser'], 'section_ids': [s.id for s in sections]})
        self.ensure_overview(session, sections, plan)
        self.start_round(session, sections, 1)
        return session

    def start_round(self, session, sections, number):
        round = self.add(m.Round, session_id=session.id, round_number=number)
        for section in sections:
            self.add(m.SectionReview, round_id=round.id, section_id=section.id)
        session.status = 'ACTIVE'
        session.current_round_number = number
        session.current_section_id = sections[0].id
        return round

    def get_current_state(self, session):
        lesson = self.one(m.LessonPlan, session_id=session.id)
        round = self.one(m.Round, session_id=session.id, round_number=session.current_round_number)
        sections = self.sections(lesson.id)
        result = []
        for section in sections:
            review = self.one(m.SectionReview, round_id=round.id, section_id=section.id)
            suggestions = self.db.scalars(select(m.Suggestion).where(m.Suggestion.user_id == self.current_user.id, m.Suggestion.round_id == round.id,
                m.Suggestion.section_id == section.id).order_by(m.Suggestion.suggestion_index)).all()
            threads = {}
            for suggestion in suggestions:
                threads.setdefault(suggestion.thread_id, []).append(suggestion)
            visible = []
            for chain in sorted(threads.values(), key=lambda items: items[0].suggestion_index):
                chain.sort(key=lambda item: item.attempt_number)
                latest = chain[-1]
                visible.append({**dump(latest),
                    'decision': (dump(d) if (d := self.one(m.Decision, suggestion_id=latest.id)) else None),
                    'history': [{**dump(item),
                        'decision': (dump(old) if (old := self.one(m.Decision, suggestion_id=item.id)) else None),
                        'custom_prompt': (dump(prompt) if (prompt := self.one(m.CustomPrompt, suggestion_id=item.id)) else None)}
                        for item in chain]})
            result.append({**dump(section), 'version': dump(self.latest(section.id)), 'review': dump(review),
                'suggestions': visible})
        document = self.one(m.UploadedDocument, session_id=session.id)
        current_job = None
        if session.current_section_id:
            current_job = self.db.scalar(select(m.GenerationJob).where(
                m.GenerationJob.user_id == self.current_user.id,
                m.GenerationJob.session_id == session.id,
                m.GenerationJob.round_id == round.id,
                m.GenerationJob.section_id == session.current_section_id)
                .order_by(m.GenerationJob.created_at.desc()).limit(1))
        overview = self.one(m.LessonOverview, session_id=session.id)
        generation = self.one(m.GenerationRecord, id=current_job.generation_id) if current_job else None
        return {'session': dump(session), 'lesson_plan': dump(lesson), 'round': dump(round), 'sections': result,
                'source_document': document_dump(document) if document else None,
                'generation_job': job_dump(current_job, generation) if current_job else None,
                'analysis_plan': overview.content.get('analysis_plan') if overview else None,
                'lesson_plan_versions': [dump(v) for v in sorted(self.rows(m.LessonPlanVersion, lesson_plan_id=lesson.id),
                                                               key=lambda v: v.round_number)]}

    def resume_session(self, session_id):
        return self.get_current_state(self.session(session_id))

    def active_target(self, session, round_id, section_id):
        if session.status != 'ACTIVE':
            raise WorkflowError('当前会话不处于可修改状态。')
        round = self.one(m.Round, id=round_id, session_id=session.id)
        if not round or round.round_number != session.current_round_number or round.status != 'ACTIVE':
            raise WorkflowError('轮次已变化，请恢复最新状态。')
        if session.current_section_id != section_id:
            raise WorkflowError('单元已变化，请恢复最新状态。')
        section = self.one(m.Section, id=section_id)
        review = self.one(m.SectionReview, round_id=round_id, section_id=section_id)
        if not section or not review or review.completed_at:
            raise WorkflowError('此单元已完成或不存在。')
        return round, section, review

    def viewed(self, session, round_id, section_id, section_version_id, suggestion_ids, decision_ids):
        # Explicit client acknowledgement of rendered content, including on recovery.
        round = self.one(m.Round, id=round_id, session_id=session.id)
        review = self.one(m.SectionReview, round_id=round_id, section_id=section_id)
        if not round or not review:
            raise WorkflowError('单元不属于此会话。', 404)
        version = self.one(m.SectionVersion, id=section_version_id, section_id=section_id)
        valid_suggestions = {s.id for s in self.rows(m.Suggestion, round_id=round_id, section_id=section_id)}
        valid_decisions = {d.id for d in self.rows(m.Decision, session_id=session.id, round_id=round_id,
                                                  section_id=section_id) if d.suggestion_id in suggestion_ids}
        if not version or not set(suggestion_ids) <= valid_suggestions or not set(decision_ids) <= valid_decisions:
            raise WorkflowError('显示确认包含不属于本单元的版本、建议或决定。', 422)
        # Record exactly what the client rendered, even if another tab has advanced the DB.
        self.event(session, 'SECTION_VIEWED', {'section_version_id': section_version_id,
            'suggestion_ids': suggestion_ids, 'decision_ids': decision_ids}, round_id, section_id)

    @staticmethod
    def _refinement_chunk(chunks, refinement):
        if len(chunks) <= 1:
            return chunks[0]
        previous = refinement['previous_suggestion']
        target = previous.get('target_text') or ''
        if target:
            located = next((chunk for chunk in chunks if target in chunk.content), None)
            if located:
                return located
        seed = ''.join(ch for ch in (previous['issue'] + previous['reason'] + previous['revision']) if not ch.isspace())
        pairs = {seed[i:i + 2] for i in range(max(0, len(seed) - 1))}
        return max(chunks, key=lambda chunk: sum(pair in chunk.content for pair in pairs))

    def _generation_contexts(self, session, round, section, audit, refinement=None):
        config = self.config(session)
        budget = session.config_snapshot.get('max_context_bytes', session.config_snapshot.get('max_context_tokens', 12000))
        system_prompt = REFINEMENT_SYSTEM_PROMPT if refinement else SYSTEM_PROMPT
        chunk_budget = content_budget(budget, system_prompt)
        if refinement:
            refinement_bytes = len(json.dumps(refinement, ensure_ascii=False).encode('utf-8'))
            chunk_budget = max(512, chunk_budget - refinement_bytes - 256)
        all_chunks = split_text(section.current_content, chunk_budget)
        chunks = [self._refinement_chunk(all_chunks, refinement)] if refinement else all_chunks
        if len(all_chunks) > session.config_snapshot.get('max_analysis_chunks_per_section', 24):
            raise ProviderError('ANALYSIS_LIMIT', '当前单元需要的内部分析块超过安全上限，请缩短后重试。')
        sections = self.sections(section.lesson_plan_id)
        overview = self.ensure_overview(session, sections)
        snapshots = [{**dump(s), 'version_id': self.latest(s.id).id} for s in sections]
        contexts = []
        for chunk in chunks:
            current = next(s for s in snapshots if s['id'] == section.id).copy()
            current['current_content'] = chunk.content
            current['analysis_chunk'] = chunk.as_dict()
            request = ContextBuildRequest(user_id=self.current_user.id, session=dump(session), round=dump(round),
                section=current, all_sections=snapshots, overview=dump(overview), refinement=refinement)
            context = config.context_engine.prepare(request, audit, system_prompt=system_prompt)
            context.user['analysis_chunk'] = {**chunk.as_dict(), 'count': len(all_chunks),
                'selected_for_refinement': bool(refinement)}
            contexts.append(context)
        return config, contexts

    @staticmethod
    def _merge_suggestions(groups):
        merged, seen = [], set()
        for group in groups:
            for suggestion in group:
                key = (' '.join(suggestion.issue.split()).lower(), ' '.join(suggestion.revision.split()).lower())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(suggestion)
                if len(merged) == 2:
                    return merged
        return merged

    def generate(self, session, round_id, section_id):
        round, section, review = self.active_target(session, round_id, section_id)
        if review.generated_at:
            return None, None
        audit = GenerationAudit(self, session, round, section, review)
        active = self.db.scalar(select(m.GenerationJob).where(m.GenerationJob.user_id == self.current_user.id,
            m.GenerationJob.round_id == round.id, m.GenerationJob.section_id == section.id,
            m.GenerationJob.status.in_(['QUEUED', 'RUNNING'])).order_by(m.GenerationJob.created_at.desc()).limit(1))
        if active:
            return active, None
        generation = None
        try:
            config, contexts = self._generation_contexts(session, round, section, audit)
            payload = contexts[0].model_dump(mode='json') if len(contexts) == 1 else {
                'mode': 'chunked', 'merge_policy': 'stable_exact_dedup_first_two',
                'contexts': [context.model_dump(mode='json') for context in contexts]}
            generation = audit.started(payload)
            if session.config_snapshot['provider'] == 'mock':
                groups = [config.suggestion_provider.generate(context) for context in contexts]
                merged = self._merge_suggestions(groups)
                audit.completed(generation, merged, None if len(contexts) == 1 else {
                    'merge_policy': 'stable_exact_dedup_first_two',
                    'merged_suggestions': [item.model_dump(mode='json') for item in merged]})
                return None, None
            job = self.add(m.GenerationJob, session_id=session.id, round_id=round.id, section_id=section.id,
                section_version_id=generation.section_version_id, generation_id=generation.id,
                context_json=payload, status='QUEUED')
            self.event(session, 'SUGGESTION_GENERATION_QUEUED', {'job_id': job.id,
                'generation_id': generation.id, 'analysis_chunk_count': len(contexts)}, round.id, section.id)
            return job, None
        except ProviderError as error:
            audit.failed(generation, error)
            return None, error

    def latest_suggestion(self, thread_id):
        return self.db.scalar(select(m.Suggestion).where(
            m.Suggestion.user_id == self.current_user.id,
            m.Suggestion.thread_id == thread_id).order_by(
                m.Suggestion.attempt_number.desc(), m.Suggestion.created_at.desc()).limit(1))

    def refinement_request(self, suggestion, custom):
        return {'prompt_text': custom.prompt_text,
            'previous_suggestion': {key: getattr(suggestion, key) for key in SuggestionDraft.model_fields},
            'root_issue': self.db.scalar(select(m.Suggestion.issue).where(
                m.Suggestion.user_id == self.current_user.id,
                m.Suggestion.thread_id == suggestion.thread_id,
                m.Suggestion.attempt_number == 1)),
            'attempt_number': suggestion.attempt_number + 1,
            'source_ids': [suggestion.id, custom.id]}

    def customize(self, session, suggestion_id, prompt_text, request_key):
        existing_prompt = self.one(m.CustomPrompt, request_key=request_key)
        if existing_prompt:
            if existing_prompt.suggestion_id != suggestion_id or existing_prompt.prompt_text != prompt_text:
                raise WorkflowError('此自定义请求编号已用于其他内容。')
            generation = self.one(m.GenerationRecord, id=existing_prompt.generation_id)
            job = self.one(m.GenerationJob, generation_id=generation.id) if generation else None
            return job, None
        suggestion = self.one(m.Suggestion, id=suggestion_id)
        if not suggestion or not self.one(m.Round, id=suggestion.round_id, session_id=session.id):
            raise WorkflowError('找不到此会话的建议。', 404)
        if self.latest_suggestion(suggestion.thread_id).id != suggestion.id:
            raise WorkflowError('这不是当前候选建议，请恢复最新状态。')
        if self.one(m.Decision, suggestion_id=suggestion.id):
            raise WorkflowError('当前候选已经作出决定。')
        round, section, review = self.active_target(session, suggestion.round_id, suggestion.section_id)
        active = self.db.scalar(select(m.GenerationJob).where(
            m.GenerationJob.user_id == self.current_user.id,
            m.GenerationJob.round_id == round.id, m.GenerationJob.section_id == section.id,
            m.GenerationJob.status.in_(['QUEUED', 'RUNNING'])).limit(1))
        if active:
            raise WorkflowError('当前单元已有生成任务，请等待完成。')
        custom = self.add(m.CustomPrompt, session_id=session.id, round_id=round.id, section_id=section.id,
            suggestion_id=suggestion.id, request_key=request_key, prompt_text=prompt_text)
        refinement = self.refinement_request(suggestion, custom)
        audit = GenerationAudit(self, session, round, section, review, 'REFINEMENT', suggestion, custom)
        try:
            config, contexts = self._generation_contexts(session, round, section, audit, refinement)
            payload = contexts[0].model_dump(mode='json')
            generation = audit.started(payload)
        except ProviderError as error:
            raise WorkflowError(str(error), 422) from error
        decision = self.add(m.Decision, suggestion_id=suggestion.id, session_id=session.id,
            round_id=round.id, section_id=section.id, decision='CUSTOM')
        self.chat(session, 'user', prompt_text, 'custom_prompt', section.id, suggestion.id)
        self.event(session, 'SUGGESTION_CUSTOM_REQUESTED', {'suggestion_id': suggestion.id,
            'decision_id': decision.id, 'custom_prompt_id': custom.id, 'generation_id': generation.id,
            'attempt_number': suggestion.attempt_number + 1}, round.id, section.id)
        if session.config_snapshot['provider'] == 'mock':
            try:
                drafts = config.suggestion_provider.generate(contexts[0])
                audit.completed(generation, drafts, getattr(config.suggestion_provider, 'last_raw', None))
                return None, None
            except ProviderError as error:
                audit.failed(generation, error)
                return None, error
        job = self.add(m.GenerationJob, session_id=session.id, round_id=round.id, section_id=section.id,
            section_version_id=generation.section_version_id, generation_id=generation.id,
            context_json=payload, status='QUEUED')
        self.event(session, 'SUGGESTION_REFINEMENT_QUEUED', {'job_id': job.id,
            'generation_id': generation.id, 'parent_suggestion_id': suggestion.id}, round.id, section.id)
        return job, None

    def retry_custom(self, session, suggestion_id):
        suggestion = self.one(m.Suggestion, id=suggestion_id)
        decision = self.one(m.Decision, suggestion_id=suggestion_id) if suggestion else None
        custom = self.one(m.CustomPrompt, suggestion_id=suggestion_id) if suggestion else None
        generation = self.one(m.GenerationRecord, id=custom.generation_id) if custom and custom.generation_id else None
        job = self.one(m.GenerationJob, generation_id=generation.id) if generation else None
        if (not suggestion or not decision or decision.decision != 'CUSTOM' or not generation
                or generation.generation_kind != 'REFINEMENT' or not job):
            raise WorkflowError('找不到可重试的自定义生成任务。', 404)
        self.active_target(session, suggestion.round_id, suggestion.section_id)
        if job.status not in ('FAILED', 'STALE'):
            raise WorkflowError('只有失败或过期的自定义任务可以重试。')
        if self.db.scalar(select(m.Suggestion.id).where(m.Suggestion.parent_suggestion_id == suggestion.id)):
            raise WorkflowError('新的候选建议已经生成。')
        if job.status == 'FAILED':
            job.status, job.error_code = 'QUEUED', None
            job.lease_expires_at = job.finished_at = None
            generation.error_code = generation.raw_response = None
            queued = job
        else:
            round, section, review = self.active_target(session, suggestion.round_id, suggestion.section_id)
            audit = GenerationAudit(self, session, round, section, review, 'REFINEMENT', suggestion, custom)
            try:
                _, contexts = self._generation_contexts(session, round, section, audit,
                    self.refinement_request(suggestion, custom))
                payload = contexts[0].model_dump(mode='json')
                replacement = audit.started(payload)
            except ProviderError as error:
                raise WorkflowError(str(error), 422) from error
            queued = self.add(m.GenerationJob, session_id=session.id, round_id=round.id,
                section_id=section.id, section_version_id=replacement.section_version_id,
                generation_id=replacement.id, context_json=payload, status='QUEUED')
        self.event(session, 'SUGGESTION_REFINEMENT_REQUEUED', {'job_id': queued.id,
            'parent_suggestion_id': suggestion.id}, suggestion.round_id, suggestion.section_id)
        return queued

    def decide(self, session, suggestion_id, decision, confirm_append=False, expected_version_id=None):
        suggestion = self.one(m.Suggestion, id=suggestion_id)
        if not suggestion or not self.one(m.Round, id=suggestion.round_id, session_id=session.id):
            raise WorkflowError('找不到此会话的建议。', 404)
        if session.status == 'TERMINATED':
            raise WorkflowError('会话已结束，不能再修改。')
        if self.latest_suggestion(suggestion.thread_id).id != suggestion.id:
            raise WorkflowError('旧版候选不能再操作，请使用当前候选。')
        active_job = self.db.scalar(select(m.GenerationJob).where(
            m.GenerationJob.user_id == self.current_user.id,
            m.GenerationJob.round_id == suggestion.round_id,
            m.GenerationJob.section_id == suggestion.section_id,
            m.GenerationJob.status.in_(['QUEUED', 'RUNNING'])).limit(1))
        if active_job:
            raise WorkflowError('当前单元仍在生成候选，请等待完成后再操作。')
        existing = self.one(m.Decision, suggestion_id=suggestion.id)
        if existing:
            if existing.decision != decision:
                raise WorkflowError('已保存的决定不可更改。')
            return
        round, section, _ = self.active_target(session, suggestion.round_id, suggestion.section_id)
        content = section.current_content
        if decision == 'ACCEPT':
            draft = SuggestionDraft(**{key: getattr(suggestion, key) for key in SuggestionDraft.model_fields})
            if confirm_append:
                if expected_version_id != self.latest(section.id).id:
                    raise WorkflowError('正文已变化，请恢复最新状态后重新确认。')
                draft = draft.model_copy(update={'revision_mode': 'append'})
            result = self.config(session).revision_strategy.apply(section.current_content, draft)
            if result.status == 'candidate':
                self.event(session, 'REVISION_CANDIDATE_SHOWN', {'suggestion_id': suggestion.id,
                    'candidate': result.candidate, 'reason': result.reason}, round.id, section.id)
                return {'suggestion_id': suggestion.id, 'candidate': result.candidate, 'reason': result.reason,
                        'expected_version_id': self.latest(section.id).id}
            content = result.content
        saved = self.add(m.Decision, suggestion_id=suggestion.id, session_id=session.id,
                         round_id=round.id, section_id=section.id, decision=decision)
        self.chat(session, 'user', decision, 'decision', section.id, suggestion.id)
        self.event(session, 'SUGGESTION_ACCEPTED' if decision == 'ACCEPT' else 'SUGGESTION_REJECTED',
                   {'suggestion_id': suggestion.id, 'decision_id': saved.id, 'confirmed_append': confirm_append}, round.id, section.id)
        if decision == 'ACCEPT':
            if content != section.current_content:
                previous = self.latest(section.id)
                version = self.add(m.SectionVersion, section_id=section.id, round_number=round.round_number,
                    version_number=previous.version_number + 1, previous_version_id=previous.id, content=content)
                section.current_content = content
                lesson = self.one(m.LessonPlan, session_id=session.id)
                lesson.current_content = ''.join(s.current_content for s in self.sections(lesson.id))
                self.event(session, 'SECTION_UPDATED', {'previous_version_id': previous.id, 'version_id': version.id,
                    'suggestion_id': suggestion.id, 'decision_id': saved.id}, round.id, section.id)

    def complete_section(self, session, round_id, section_id):
        if session.status == 'TERMINATED':
            raise WorkflowError('会话已结束。')
        owned = self.one(m.Round, id=round_id, session_id=session.id)
        review = self.one(m.SectionReview, round_id=round_id, section_id=section_id) if owned else None
        if review and review.completed_at:
            return  # Retry cannot advance a second section.
        round, section, review = self.active_target(session, round_id, section_id)
        if not review.generated_at:
            raise WorkflowError('请先生成建议，即使没有建议也需要记录生成结果。')
        suggestions = self.rows(m.Suggestion, round_id=round.id, section_id=section.id)
        leaves = [self.latest_suggestion(thread_id) for thread_id in {s.thread_id for s in suggestions}]
        decisions = [self.one(m.Decision, suggestion_id=s.id) for s in leaves]
        if any(not decision or decision.decision not in ('ACCEPT', 'REJECT') for decision in decisions):
            raise WorkflowError('请先对每条当前建议选择采纳或拒绝；自定义生成尚未完成。')
        review.completed_at = m.now()
        self.event(session, 'SECTION_COMPLETED', {'section_version_id': self.latest(section.id).id}, round.id, section.id)
        sections = self.sections(section.lesson_plan_id)
        next_section = next((s for s in sections if s.order_index > section.order_index), None)
        if next_section:
            session.current_section_id = next_section.id
        else:
            round.status, round.completed_at = 'COMPLETED', m.now()
            session.status, session.current_section_id = 'ROUND_COMPLETED', None
            lesson = self.one(m.LessonPlan, session_id=session.id)
            version = self.add(m.LessonPlanVersion, lesson_plan_id=lesson.id,
                               round_number=round.round_number, content=lesson.current_content)
            self.event(session, 'ROUND_COMPLETED', {'lesson_plan_version_id': version.id}, round.id)

    def continue_round(self, session, round_id):
        if session.status == 'TERMINATED':
            raise WorkflowError('会话已结束。')
        previous = self.one(m.Round, id=round_id, session_id=session.id)
        if not previous or previous.status != 'COMPLETED':
            raise WorkflowError('请先完成本轮。')
        if previous.round_number >= self.config(session).max_rounds:
            raise WorkflowError('已达到最多五轮，不能进入第六轮。')
        if self.one(m.Round, session_id=session.id, round_number=previous.round_number + 1):
            return  # Idempotent retry using the explicit previous round ID.
        if session.status != 'ROUND_COMPLETED' or session.current_round_number != previous.round_number:
            raise WorkflowError('轮次状态不匹配。')
        lesson = self.one(m.LessonPlan, session_id=session.id)
        round = self.start_round(session, self.sections(lesson.id), previous.round_number + 1)
        self.event(session, 'ROUND_CONTINUED', {'previous_round_id': previous.id, 'round_number': round.round_number}, round.id)

    def terminate_session(self, session):
        if session.status == 'TERMINATED':
            return
        if session.status != 'ROUND_COMPLETED':
            raise WorkflowError('请先完成本轮所有单元，再结束会话。')
        session.status, session.terminated_at = 'TERMINATED', m.now()
        round = self.one(m.Round, session_id=session.id, round_number=session.current_round_number)
        self.event(session, 'SESSION_TERMINATED', {'final_round_number': session.current_round_number}, round.id)

    def export(self, session):
        state = self.get_current_state(session)
        section_ids = [s['id'] for s in state['sections']]
        result = {'schema_version': 2, 'user_id': self.current_user.id, 'state': state, 'participant': dump(self.one(m.Participant, id=session.participant_id))}
        for cls in (m.Round, m.Decision, m.CustomPrompt, m.RetrievalRecord, m.GenerationRecord,
                    m.GenerationJob, m.ContextSnapshot, m.SystemConfigSnapshot, m.LessonOverview):
            result[cls.__tablename__] = [dump(x) for x in self.rows(cls, session_id=session.id)]
        result['interaction_events'] = [dump(x) for x in self.db.scalars(select(m.InteractionEvent)
            .where(m.InteractionEvent.session_id == session.id, m.InteractionEvent.user_id == self.current_user.id).order_by(m.InteractionEvent.sequence))]
        for cls in (m.SectionVersion, m.Suggestion, m.SectionReview):
            query = select(cls).where(cls.section_id.in_(section_ids), cls.user_id == self.current_user.id).order_by(cls.created_at, cls.id)
            result[cls.__tablename__] = [dump(x) for x in self.db.scalars(query)]
        conversation = self.one(m.Conversation, session_id=session.id)
        result['chat_messages'] = [dump(x) for x in self.rows(m.ChatMessage, conversation_id=conversation.id)] if conversation else []
        document = self.one(m.UploadedDocument, session_id=session.id)
        result['uploaded_documents'] = [document_dump(document, include_text=True)] if document else []
        ocr_job = self.one(m.DocumentProcessingJob, document_id=document.id) if document else None
        result['document_processing_jobs'] = [dump(ocr_job)] if ocr_job else []
        result['summary'] = {'section_count': len(section_ids), 'round_count': len(result['rounds']),
            'suggestions': len(result['suggestions']), 'accepts': sum(x['decision'] == 'ACCEPT' for x in result['decisions']),
            'rejects': sum(x['decision'] == 'REJECT' for x in result['decisions']),
            'custom_refinements': sum(x['decision'] == 'CUSTOM' for x in result['decisions']),
            'duration_seconds': max(0, ((session.terminated_at or m.now()).replace(tzinfo=timezone.utc) - session.created_at.replace(tzinfo=timezone.utc)).total_seconds())}
        result['failures'] = [x for x in result['generation_records'] if x['error_code']]
        return result

    def ensure_overview(self, session, sections, analysis_plan=None):
        overview = self.one(m.LessonOverview, session_id=session.id)
        if overview: return overview
        def excerpts(types):
            return [{'section_id': s.id, 'excerpt': s.current_content[:800], 'truncated': len(s.current_content) > 800}
                    for s in sections if s.section_type in types]
        return self.add(m.LessonOverview, session_id=session.id, content={
            **session.lesson_metadata, 'section_list': [{'id': s.id, 'title': s.title, 'type': s.section_type} for s in sections],
            'recognized_objectives': excerpts(['objectives']), 'activity_overview': excerpts(['exploration','practice','teaching_process']),
            'assessment_overview': excerpts(['assessment']), 'short_lesson_summary': ' / '.join(s.title for s in sections),
            'basis': 'section_excerpts_at_overview_creation', 'generated_once': True,
            'analysis_plan': analysis_plan})

    def chat(self, session, role, content, message_type, section_id=None, suggestion_id=None):
        conversation = self.one(m.Conversation, session_id=session.id)
        if not conversation: conversation = self.add(m.Conversation, session_id=session.id)
        return self.add(m.ChatMessage, conversation_id=conversation.id, role=role, content=content,
            message_type=message_type, related_section_id=section_id, related_suggestion_id=suggestion_id)

    def list_sessions(self):
        return [dump(s) for s in self.db.scalars(select(m.Session).where(m.Session.user_id == self.current_user.id)
            .order_by(m.Session.updated_at.desc()))]
