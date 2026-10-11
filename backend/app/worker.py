"""Durable PostgreSQL/SQLite worker for external model generation."""
import argparse
import time
from datetime import timedelta

from sqlalchemy import or_, select

from . import models as m
from .audit import GenerationAudit
from .db import SessionLocal
from .history import HistoryReader
from .providers.config import ExperimentConfigFactory
from .providers.interfaces import LLMContext
from .providers.llm import ProviderError
from .settings import settings
from .workflow import Workflow


class GenerationWorker:
    def __init__(self, session_factory=SessionLocal, factory=None, lease_seconds=1800):
        self.session_factory = session_factory
        self.factory = factory or ExperimentConfigFactory(settings)
        self.lease_seconds = lease_seconds

    def claim(self):
        now = m.now()
        with self.session_factory.begin() as db:
            statement = select(m.GenerationJob).where(or_(
                m.GenerationJob.status == 'QUEUED',
                (m.GenerationJob.status == 'RUNNING') & (m.GenerationJob.lease_expires_at < now),
            )).order_by(m.GenerationJob.created_at, m.GenerationJob.id).limit(1)
            if db.bind.dialect.name == 'postgresql':
                statement = statement.with_for_update(skip_locked=True)
            else:
                statement = statement.with_for_update()
            job = db.scalar(statement)
            if not job:
                return None
            job.status = 'RUNNING'
            job.attempt_count += 1
            job.started_at = job.started_at or now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            return job.id

    def _load(self, job_id):
        with self.session_factory() as db:
            job = db.get(m.GenerationJob, job_id)
            if not job or job.status != 'RUNNING':
                return None
            session = db.get(m.Session, job.session_id)
            user = db.get(m.User, job.user_id)
            config = self.factory.build(session.config_snapshot, HistoryReader(db, user.id))
            return job.context_json, config.suggestion_provider

    @staticmethod
    def _contexts(payload):
        values = payload.get('contexts') if payload.get('mode') == 'chunked' else [payload]
        return [LLMContext.model_validate(value) for value in values]

    @staticmethod
    def _merge(groups):
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

    def _finish(self, job_id, suggestions=None, raw=None, error=None):
        with self.session_factory.begin() as db:
            job = db.scalar(select(m.GenerationJob).where(m.GenerationJob.id == job_id).with_for_update())
            if not job or job.status != 'RUNNING':
                return
            session = db.scalar(select(m.Session).where(m.Session.id == job.session_id).with_for_update())
            user = db.get(m.User, job.user_id)
            workflow = Workflow(db, self.factory, user)
            round = workflow.one(m.Round, id=job.round_id, session_id=session.id)
            section = workflow.one(m.Section, id=job.section_id)
            review = workflow.one(m.SectionReview, round_id=job.round_id, section_id=job.section_id)
            generation = workflow.one(m.GenerationRecord, id=job.generation_id)
            parent = workflow.one(m.Suggestion, id=generation.parent_suggestion_id) if generation.parent_suggestion_id else None
            custom = workflow.one(m.CustomPrompt, id=generation.custom_prompt_id) if generation.custom_prompt_id else None
            latest = workflow.latest(job.section_id)
            now = m.now()
            invalid = (not round or not section or not review or session.status != 'ACTIVE'
                    or session.current_section_id != job.section_id or latest.id != job.section_version_id)
            if generation.generation_kind == 'INITIAL':
                invalid = invalid or review.generated_at
            else:
                decision = workflow.one(m.Decision, suggestion_id=parent.id) if parent else None
                child = workflow.one(m.Suggestion, parent_suggestion_id=parent.id) if parent else None
                invalid = invalid or not parent or not custom or not decision or decision.decision != 'CUSTOM' or bool(child)
            if invalid:
                job.status, job.finished_at, job.lease_expires_at = 'STALE', now, None
                generation.raw_response = raw
                workflow.event(session, 'SUGGESTION_GENERATION_STALE', {'job_id': job.id,
                    'expected_section_version_id': job.section_version_id,
                    'current_section_version_id': latest.id if latest else None}, job.round_id, job.section_id)
                return
            audit = GenerationAudit(workflow, session, round, section, review,
                generation.generation_kind, parent, custom)
            audit.load_sources(job.context_json)
            if error:
                audit.failed(generation, error)
                job.status, job.error_code = 'FAILED', error.code
            else:
                audit.completed(generation, suggestions or [], raw)
                job.status = 'SUCCEEDED'
            job.finished_at, job.lease_expires_at = now, None
            workflow.event(session, 'SUGGESTION_GENERATION_JOB_FINISHED', {
                'job_id': job.id, 'status': job.status}, job.round_id, job.section_id)

    def run_once(self):
        job_id = self.claim()
        if not job_id:
            return False
        try:
            loaded = self._load(job_id)
            if not loaded:
                return True
            payload, provider = loaded
            groups, raw_responses = [], []
            for context in self._contexts(payload):
                groups.append(provider.generate(context))
                raw_responses.append(getattr(provider, 'last_raw', None))
            suggestions = self._merge(groups)
            raw = raw_responses[0] if len(raw_responses) == 1 else {
                'merge_policy': 'stable_exact_dedup_first_two', 'chunk_responses': raw_responses,
                'merged_suggestions': [item.model_dump(mode='json') for item in suggestions]}
            self._finish(job_id, suggestions=suggestions, raw=raw)
        except ProviderError as error:
            self._finish(job_id, error=error, raw=error.raw)
        except Exception:
            self._finish(job_id, error=ProviderError('WORKER_FAILURE',
                '后台生成任务执行失败，状态已保留，可以重试。'))
        return True

    def run_forever(self, poll_seconds=1.0):
        while True:
            if not self.run_once():
                time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--poll-seconds', type=float, default=1.0)
    args = parser.parse_args()
    worker = GenerationWorker()
    if args.once:
        worker.run_once()
    else:
        worker.run_forever(args.poll_seconds)


if __name__ == '__main__':
    main()
