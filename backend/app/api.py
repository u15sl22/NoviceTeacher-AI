from contextlib import contextmanager
from pathlib import Path
from uuid import UUID
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text, select, func
from sqlalchemy.exc import IntegrityError, OperationalError
from .db import SessionLocal
from .settings import settings
from .schemas import CreateSession, Decide, ReviewTarget, ViewAck
from .providers.config import ExperimentConfigFactory
from .workflow import Workflow, WorkflowError
from .auth import AUTH_PROVIDERS
from . import models as m


def create_app(session_factory=SessionLocal, config_factory=None, auth_provider=None):
    app = FastAPI(title='PedagoLoop', version='0.2.0-alpha')
    factory = config_factory or ExperimentConfigFactory(settings)
    auth = auth_provider or AUTH_PROVIDERS[factory.settings.auth_provider](factory.settings)

    @contextmanager
    def transaction(request):
        with session_factory() as db:
            try:
                if db.bind.dialect.name == 'sqlite':
                    db.execute(text('BEGIN IMMEDIATE'))
                yield Workflow(db, factory, auth.get_current_user(db, request))
                db.commit()
            except WorkflowError as exc:
                db.rollback()
                raise HTTPException(exc.status, str(exc)) from exc
            except (IntegrityError, OperationalError) as exc:
                db.rollback()
                raise HTTPException(409, '请求冲突或数据库暂不可用，请恢复状态后重试。') from exc

    @app.get('/api/health')
    def health():
        with session_factory() as db:
            db.execute(text('SELECT 1'))
        return {'status': 'ok', 'provider': factory.settings.suggestion_provider,
                'llm_configured': bool(factory.settings.llm_api_key), 'max_rounds': 5}

    @app.get('/api/me')
    def me(request: Request):
        with transaction(request) as w:
            return {'id': w.current_user.id, 'username': w.current_user.username, 'role': w.current_user.role}

    @app.get('/api/capabilities')
    def capabilities(request: Request):
        with transaction(request) as w:
            return {'contributors': factory.settings.context_contributors,
                'verified_cases': w.db.scalar(select(func.count()).select_from(m.CaseItem).join(m.DatasetAnnotation,
                    m.CaseItem.source_annotation_id == m.DatasetAnnotation.id).where(m.CaseItem.verification_status == 'verified',
                        m.DatasetAnnotation.verification_status == 'verified')),
                'verified_knowledge': w.db.scalar(select(func.count()).select_from(m.KnowledgeItem).where(m.KnowledgeItem.verification_status == 'verified')),
                'raw_annotations': w.db.scalar(select(func.count()).select_from(m.DatasetAnnotation).where(m.DatasetAnnotation.verification_status == 'raw'))}

    @app.get('/api/sessions')
    def history(request: Request):
        with transaction(request) as w:
            return w.list_sessions()

    @app.get('/api/sessions/{session_id}/chat')
    def chat(request: Request, session_id: UUID):
        with transaction(request) as w:
            return w.export(w.session(str(session_id)))['chat_messages']

    @app.post('/api/sessions')
    def create_session(request: Request, data: CreateSession):
        with transaction(request) as w:
            return w.get_current_state(w.create_session(data))

    @app.get('/api/sessions/{session_id}/current-state')
    def current_state(request: Request, session_id: UUID):
        with transaction(request) as w:
            return w.resume_session(str(session_id))

    @app.post('/api/sessions/{session_id}/view')
    def view(request: Request, session_id: UUID, target: ViewAck):
        with transaction(request) as w:
            w.viewed(w.session(str(session_id)), str(target.round_id), str(target.section_id),
                     str(target.section_version_id), [str(x) for x in target.suggestion_ids],
                     [str(x) for x in target.decision_ids])
        return {'ok': True}

    @app.post('/api/sessions/{session_id}/suggestions')
    def generate(request: Request, session_id: UUID, target: ReviewTarget):
        with transaction(request) as w:
            session = w.session(str(session_id))
            error = w.generate(session, str(target.round_id), str(target.section_id))
            state = w.get_current_state(session)
        # Provider failure is committed as history before returning an error response.
        if error:
            return JSONResponse(status_code=502, content={'detail': str(error), 'code': error.code})
        return state

    @app.post('/api/sessions/{session_id}/suggestions/{suggestion_id}/decision')
    def decide(request: Request, session_id: UUID, suggestion_id: UUID, data: Decide):
        with transaction(request) as w:
            session = w.session(str(session_id))
            candidate = w.decide(session, str(suggestion_id), data.decision, data.confirm_append,
                                 str(data.expected_version_id) if data.expected_version_id else None)
            return {**w.get_current_state(session), 'revision_candidate': candidate} if candidate else w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/complete-section')
    def complete_section(request: Request, session_id: UUID, target: ReviewTarget):
        with transaction(request) as w:
            session = w.session(str(session_id))
            w.complete_section(session, str(target.round_id), str(target.section_id))
            return w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/rounds/{round_id}/continue')
    def continue_round(request: Request, session_id: UUID, round_id: UUID):
        with transaction(request) as w:
            session = w.session(str(session_id))
            w.continue_round(session, str(round_id))
            return w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/terminate')
    def terminate(request: Request, session_id: UUID):
        with transaction(request) as w:
            session = w.session(str(session_id))
            w.terminate_session(session)
            return w.get_current_state(session)

    @app.get('/api/sessions/{session_id}/final')
    def final(request: Request, session_id: UUID):
        with transaction(request) as w:
            session = w.session(str(session_id))
            if session.status != 'TERMINATED':
                raise WorkflowError('会话尚未结束。')
            return w.get_current_state(session)

    @app.get('/api/sessions/{session_id}/export')
    def export(request: Request, session_id: UUID):
        with transaction(request) as w:
            result = w.export(w.session(str(session_id)))
        return JSONResponse(content=jsonable_encoder(result), headers={
            'Content-Disposition': f'attachment; filename="pedago-loop-{session_id}.json"'})

    @app.post('/api/sessions/{session_id}/custom-prompt')
    def inquiry(request: Request, session_id: UUID):
        with transaction(request) as w:
            w.session(str(session_id))
            raise WorkflowError('此演示配置未启用主动探询。', 403)

    frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if frontend.exists():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app


app = create_app()
