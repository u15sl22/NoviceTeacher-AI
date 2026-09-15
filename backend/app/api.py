from contextlib import contextmanager
from pathlib import Path
from uuid import UUID
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from .db import SessionLocal
from .settings import settings
from .schemas import CreateSession, Decide, ReviewTarget, ViewAck
from .providers.config import ExperimentConfigFactory
from .workflow import Workflow, WorkflowError


def create_app(session_factory=SessionLocal, config_factory=None):
    app = FastAPI(title='PedagoLoop', version='0.1.0')
    factory = config_factory or ExperimentConfigFactory(settings)

    @contextmanager
    def transaction():
        with session_factory() as db:
            try:
                if db.bind.dialect.name == 'sqlite':
                    db.execute(text('BEGIN IMMEDIATE'))
                yield Workflow(db, factory)
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
        return {'status': 'ok', 'provider': settings.suggestion_provider,
                'llm_configured': bool(settings.llm_api_key), 'max_rounds': 5}

    @app.post('/api/sessions')
    def create_session(data: CreateSession):
        with transaction() as w:
            return w.get_current_state(w.create_session(data))

    @app.get('/api/sessions/{session_id}/current-state')
    def current_state(session_id: UUID):
        with transaction() as w:
            return w.resume_session(str(session_id))

    @app.post('/api/sessions/{session_id}/view')
    def view(session_id: UUID, target: ViewAck):
        with transaction() as w:
            w.viewed(w.session(str(session_id)), str(target.round_id), str(target.section_id),
                     str(target.section_version_id), [str(x) for x in target.suggestion_ids],
                     [str(x) for x in target.decision_ids])
        return {'ok': True}

    @app.post('/api/sessions/{session_id}/suggestions')
    def generate(session_id: UUID, target: ReviewTarget):
        with transaction() as w:
            session = w.session(str(session_id))
            error = w.generate(session, str(target.round_id), str(target.section_id))
            state = w.get_current_state(session)
        # Provider failure is committed as history before returning an error response.
        if error:
            return JSONResponse(status_code=502, content={'detail': str(error), 'code': error.code})
        return state

    @app.post('/api/sessions/{session_id}/suggestions/{suggestion_id}/decision')
    def decide(session_id: UUID, suggestion_id: UUID, data: Decide):
        with transaction() as w:
            session = w.session(str(session_id))
            w.decide(session, str(suggestion_id), data.decision)
            return w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/complete-section')
    def complete_section(session_id: UUID, target: ReviewTarget):
        with transaction() as w:
            session = w.session(str(session_id))
            w.complete_section(session, str(target.round_id), str(target.section_id))
            return w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/rounds/{round_id}/continue')
    def continue_round(session_id: UUID, round_id: UUID):
        with transaction() as w:
            session = w.session(str(session_id))
            w.continue_round(session, str(round_id))
            return w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/terminate')
    def terminate(session_id: UUID):
        with transaction() as w:
            session = w.session(str(session_id))
            w.terminate_session(session)
            return w.get_current_state(session)

    @app.get('/api/sessions/{session_id}/final')
    def final(session_id: UUID):
        with transaction() as w:
            session = w.session(str(session_id))
            if session.status != 'TERMINATED':
                raise WorkflowError('会话尚未结束。')
            return w.get_current_state(session)

    @app.get('/api/sessions/{session_id}/export')
    def export(session_id: UUID):
        with transaction() as w:
            result = w.export(w.session(str(session_id)))
        return JSONResponse(content=jsonable_encoder(result), headers={
            'Content-Disposition': f'attachment; filename="pedago-loop-{session_id}.json"'})

    @app.post('/api/sessions/{session_id}/custom-prompt')
    def inquiry(session_id: UUID):
        with transaction() as w:
            w.session(str(session_id))
            raise WorkflowError('此演示配置未启用主动探询。', 403)

    frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if frontend.exists():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app


app = create_app()
