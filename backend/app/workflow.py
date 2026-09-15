"""Stable workflow. Every mutation runs under a DB transaction and session lock.

PostgreSQL locks one session row; local SQLite uses BEGIN IMMEDIATE. The bounded
LLM call deliberately holds that lock in this MVP to prevent duplicate generations.
"""
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select, func
from . import models as m
from .history import HistoryReader
from .orchestrator import ReviewOrchestrator
from .providers.interfaces import SuggestionDraft


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


class Workflow:
    def __init__(self, db, factory):
        self.db, self.factory = db, factory

    def add(self, cls, **values):
        row = cls(**values)
        self.db.add(row)
        self.db.flush()
        return row

    def one(self, cls, **where):
        return self.db.scalar(select(cls).filter_by(**where))

    def rows(self, cls, **where):
        return self.db.scalars(select(cls).filter_by(**where).order_by(cls.created_at, cls.id)).all()

    def session(self, session_id):
        row = self.db.scalar(select(m.Session).where(m.Session.id == session_id).with_for_update())
        if not row:
            raise WorkflowError('找不到此会话，请检查恢复链接。', 404)
        return row

    def config(self, session):
        return self.factory.build(session.config_snapshot, HistoryReader(self.db))

    def event(self, session, event_type, payload=None, round_id=None, section_id=None):
        sequence = self.db.scalar(select(func.coalesce(func.max(m.InteractionEvent.sequence), 0))
                                  .where(m.InteractionEvent.session_id == session.id)) + 1
        session.updated_at = m.now()
        return self.add(m.InteractionEvent, participant_id=session.participant_id, session_id=session.id,
            round_id=round_id, section_id=section_id, sequence=sequence,
            event_type=event_type, event_payload=payload or {})

    def sections(self, lesson_id):
        return self.db.scalars(select(m.Section).where(m.Section.lesson_plan_id == lesson_id)
                               .order_by(m.Section.order_index)).all()

    def latest(self, section_id):
        return self.db.scalar(select(m.SectionVersion).where(m.SectionVersion.section_id == section_id)
                              .order_by(m.SectionVersion.version_number.desc()).limit(1))

    def create_session(self, data):
        payload = data.model_dump(mode='json')
        fingerprint = hashlib.sha256(json.dumps({'metadata': payload['metadata'], 'content': data.content},
                                                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = self.one(m.Session, request_key=str(data.request_key))
        if existing:
            if existing.input_hash != fingerprint:
                raise WorkflowError('此提交编号已经用于不同教案。请新建提交。')
            return existing
        if not data.content.strip():
            raise WorkflowError('教案不能为空。', 422)
        snapshot = self.factory.snapshot()
        participant = self.add(m.Participant)
        session = self.add(m.Session, participant_id=participant.id, request_key=str(data.request_key),
                           input_hash=fingerprint, lesson_metadata=payload['metadata'], config_snapshot=snapshot)
        self.event(session, 'SESSION_CREATED', {'config': snapshot})
        lesson = self.add(m.LessonPlan, session_id=session.id, original_content=data.content, current_content=data.content)
        original = self.add(m.LessonPlanVersion, lesson_plan_id=lesson.id, round_number=0, content=data.content)
        self.event(session, 'LESSON_PLAN_SUBMITTED', {'lesson_plan_id': lesson.id, 'version_id': original.id})
        drafts = self.config(session).section_parser.parse(data.content, payload['metadata'])
        if not drafts or ''.join(d.content for d in drafts) != data.content:
            raise WorkflowError('切分器必须返回非空列表并完整保留原文。', 422)
        sections = []
        for i, draft in enumerate(drafts):
            section = self.add(m.Section, lesson_plan_id=lesson.id, title=draft.title,
                               order_index=i, section_type=draft.section_type, current_content=draft.content)
            self.add(m.SectionVersion, section_id=section.id, round_number=0, version_number=0, content=draft.content)
            sections.append(section)
        self.event(session, 'SECTIONS_PARSED', {'parser': snapshot['section_parser'], 'section_ids': [s.id for s in sections]})
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
            suggestions = self.db.scalars(select(m.Suggestion).where(m.Suggestion.round_id == round.id,
                m.Suggestion.section_id == section.id).order_by(m.Suggestion.suggestion_index)).all()
            result.append({**dump(section), 'version': dump(self.latest(section.id)), 'review': dump(review),
                'suggestions': [{**dump(s), 'decision': (dump(d) if (d := self.one(m.Decision, suggestion_id=s.id)) else None)}
                                 for s in suggestions]})
        return {'session': dump(session), 'lesson_plan': dump(lesson), 'round': dump(round), 'sections': result,
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

    def generate(self, session, round_id, section_id):
        round, section, review = self.active_target(session, round_id, section_id)
        if review.generated_at:
            return None
        audit = GenerationAudit(self, session, round, section, review)
        _, error = ReviewOrchestrator(self.config(session)).generate(dump(session), dump(round), dump(section), audit)
        return error

    def decide(self, session, suggestion_id, decision):
        suggestion = self.one(m.Suggestion, id=suggestion_id)
        if not suggestion or not self.one(m.Round, id=suggestion.round_id, session_id=session.id):
            raise WorkflowError('找不到此会话的建议。', 404)
        if session.status == 'TERMINATED':
            raise WorkflowError('会话已结束，不能再修改。')
        existing = self.one(m.Decision, suggestion_id=suggestion.id)
        if existing:
            if existing.decision != decision:
                raise WorkflowError('已保存的决定不可更改。')
            return
        round, section, _ = self.active_target(session, suggestion.round_id, suggestion.section_id)
        saved = self.add(m.Decision, suggestion_id=suggestion.id, session_id=session.id,
                         round_id=round.id, section_id=section.id, decision=decision)
        self.event(session, 'SUGGESTION_ACCEPTED' if decision == 'ACCEPT' else 'SUGGESTION_REJECTED',
                   {'suggestion_id': suggestion.id, 'decision_id': saved.id}, round.id, section.id)
        if decision == 'ACCEPT':
            draft = SuggestionDraft(**{key: getattr(suggestion, key) for key in SuggestionDraft.model_fields})
            content = self.config(session).revision_strategy.apply(section.current_content, draft)
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
        if any(not self.one(m.Decision, suggestion_id=s.id) for s in suggestions):
            raise WorkflowError('请先对每条建议选择采纳或拒绝。')
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
        result = {'schema_version': 1, 'state': state, 'participant': dump(self.one(m.Participant, id=session.participant_id))}
        for cls in (m.Round, m.Decision, m.CustomPrompt, m.RetrievalRecord, m.GenerationRecord):
            result[cls.__tablename__] = [dump(x) for x in self.rows(cls, session_id=session.id)]
        result['interaction_events'] = [dump(x) for x in self.db.scalars(select(m.InteractionEvent)
            .where(m.InteractionEvent.session_id == session.id).order_by(m.InteractionEvent.sequence))]
        for cls in (m.SectionVersion, m.Suggestion, m.SectionReview):
            query = select(cls).where(cls.section_id.in_(section_ids)).order_by(cls.created_at, cls.id)
            result[cls.__tablename__] = [dump(x) for x in self.db.scalars(query)]
        return result


class GenerationAudit:
    def __init__(self, workflow, session, round, section, review):
        self.w, self.session, self.round, self.section, self.review = workflow, session, round, section, review

    def started(self, context, knowledge):
        self.w.add(m.RetrievalRecord, session_id=self.session.id, round_id=self.round.id,
                   section_id=self.section.id, items=[x.model_dump() for x in knowledge])
        return self.w.add(m.GenerationRecord, session_id=self.session.id, round_id=self.round.id,
            section_id=self.section.id, section_version_id=self.w.latest(self.section.id).id,
            provider_type=self.session.config_snapshot['provider'], runtime_context=context.model_dump(mode='json'))

    def failed(self, generation, error):
        generation.error_code, generation.raw_response = error.code, error.raw
        self.w.event(self.session, 'SUGGESTION_GENERATION_FAILED', {'generation_id': generation.id, 'error_code': error.code},
                     self.round.id, self.section.id)

    def completed(self, generation, suggestions, raw):
        generation.raw_response = raw or {'suggestions': [x.model_dump() for x in suggestions]}
        ids = []
        for i, draft in enumerate(suggestions, 1):
            suggestion = self.w.add(m.Suggestion, section_id=self.section.id, round_id=self.round.id,
                generation_id=generation.id, suggestion_index=i, provider_type=generation.provider_type,
                basis_type='mock' if generation.provider_type == 'mock' else 'provisional_model', **draft.model_dump())
            ids.append(suggestion.id)
        self.review.generated_at = m.now()
        self.w.event(self.session, 'SUGGESTION_GENERATED', {'generation_id': generation.id, 'suggestion_ids': ids,
                     'section_version_id': generation.section_version_id}, self.round.id, self.section.id)
