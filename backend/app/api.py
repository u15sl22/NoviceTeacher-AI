from contextlib import contextmanager
import hashlib
from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text, select, func
from sqlalchemy.exc import IntegrityError, OperationalError
from .db import SessionLocal
from .settings import settings
from .schemas import AnalysisPlanRequest, CreateSession, CustomPromptRequest, Decide, ReviewTarget, ViewAck
from .providers.config import ExperimentConfigFactory
from .workflow import Workflow, WorkflowError
from .auth import AUTH_PROVIDERS
from .documents import DocumentParseError, OCRRequired, parse_document
from .storage import LocalDocumentStorage
from . import models as m


def create_app(session_factory=SessionLocal, config_factory=None, auth_provider=None, document_storage=None):
    app = FastAPI(title='PedagoLoop', version='0.2.0-alpha')
    factory = config_factory or ExperimentConfigFactory(settings)
    auth = auth_provider or AUTH_PROVIDERS[factory.settings.auth_provider](factory.settings)
    document_store = document_storage or LocalDocumentStorage(factory.settings.storage_root)

    def document_result(w, document):
        job = w.one(m.DocumentProcessingJob, document_id=document.id)
        return {'id': document.id, 'filename': document.original_filename,
            'media_type': document.media_type, 'byte_size': document.byte_size,
            'sha256': document.sha256, 'parse_status': document.parse_status,
            'parser_name': document.parser_name, 'page_count': document.page_count,
            'text': document.extracted_text, 'char_count': len(document.extracted_text or ''),
            'error': document.parse_error,
            'processing': ({key: getattr(job, key) for key in ('status', 'attempt_count',
                'current_page', 'total_pages', 'error_code')} if job else None)}

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
        profiles = factory.public_profiles()
        with session_factory() as db:
            db.execute(text('SELECT 1'))
        return {'status': 'ok', 'provider': factory.settings.suggestion_provider,
                'llm_configured': any(item['configured'] for item in profiles),
                'ocr_enabled': factory.settings.ocr_enabled,
                'default_model_profile': next(item['id'] for item in profiles if item['default']),
                'max_rounds': 5}

    @app.get('/api/me')
    def me(request: Request):
        with transaction(request) as w:
            return {'id': w.current_user.id, 'username': w.current_user.username, 'role': w.current_user.role}

    @app.get('/api/capabilities')
    def capabilities(request: Request):
        with transaction(request) as w:
            return {'contributors': factory.settings.context_contributors,
                'model_profiles': factory.public_profiles(),
                'online_ocr': factory.settings.ocr_enabled,
                'verified_cases': w.db.scalar(select(func.count()).select_from(m.CaseItem).join(m.DatasetAnnotation,
                    m.CaseItem.source_annotation_id == m.DatasetAnnotation.id).where(m.CaseItem.verification_status == 'verified',
                        m.DatasetAnnotation.verification_status == 'verified')),
                'verified_knowledge': w.db.scalar(select(func.count()).select_from(m.KnowledgeItem).where(m.KnowledgeItem.verification_status == 'verified')),
                'raw_annotations': w.db.scalar(select(func.count()).select_from(m.DatasetAnnotation).where(m.DatasetAnnotation.verification_status == 'raw'))}

    @app.get('/api/sessions')
    def history(request: Request):
        with transaction(request) as w:
            return w.list_sessions()

    @app.post('/api/analysis-plan')
    def analysis_plan(request: Request, data: AnalysisPlanRequest):
        with transaction(request) as w:
            return w.analysis_plan(data)[0]

    @app.post('/api/documents')
    async def upload_document(request: Request, filename: str):
        name = filename.replace('\\', '/').rsplit('/', 1)[-1].strip()
        if not name or len(name) > 255 or '\x00' in name:
            raise HTTPException(422, '文件名无效。')
        length = request.headers.get('content-length')
        if length:
            try:
                if int(length) > factory.settings.max_document_bytes:
                    raise HTTPException(413, '文件超过 15 MB 限制。')
            except ValueError:
                raise HTTPException(400, 'Content-Length 无效。')
        content = await request.body()
        if not content:
            raise HTTPException(422, '文件不能为空。')
        if len(content) > factory.settings.max_document_bytes:
            raise HTTPException(413, '文件超过 15 MB 限制。')
        suffix = Path(name).suffix.lower()
        if suffix not in ('.docx', '.pdf'):
            raise HTTPException(422, '仅支持 .docx 和 .pdf 文件。')
        queued_ocr = False
        try:
            # Parsing may be CPU intensive; do not block the asyncio event loop.
            parsed = await run_in_threadpool(
                parse_document,
                content,
                name,
                factory.settings.ocr_min_text_chars_per_page,
            )
        except OCRRequired as exc:
            if not factory.settings.ocr_enabled:
                raise HTTPException(422, '服务器未启用扫描 PDF OCR。') from exc
            queued_ocr = True
            parsed = None
            page_count = exc.page_count
        except DocumentParseError as exc:
            raise HTTPException(422, str(exc)) from exc
        key = document_store.put(content, suffix)
        try:
            with transaction(request) as w:
                document = w.add(m.UploadedDocument, original_filename=name, storage_key=key,
                    media_type='application/pdf' if queued_ocr else parsed.media_type,
                    byte_size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    parse_status='queued' if queued_ocr else 'parsed',
                    parser_name=None if queued_ocr else parsed.parser_name,
                    extracted_text=None if queued_ocr else parsed.text,
                    page_count=page_count if queued_ocr else parsed.page_count)
                if queued_ocr:
                    w.add(m.DocumentProcessingJob, document_id=document.id, status='QUEUED',
                        total_pages=page_count)
                result = document_result(w, document)
        except Exception:
            document_store.delete(key)
            raise
        return JSONResponse(status_code=202, content=jsonable_encoder(result)) if queued_ocr else result

    @app.get('/api/documents/{document_id}')
    def document_status(request: Request, document_id: UUID):
        with transaction(request) as w:
            document = w.one(m.UploadedDocument, id=str(document_id))
            if not document:
                raise WorkflowError('找不到此源文件。', 404)
            return document_result(w, document)

    @app.post('/api/documents/{document_id}/retry')
    def retry_document(request: Request, document_id: UUID):
        with transaction(request) as w:
            document = w.one(m.UploadedDocument, id=str(document_id))
            job = w.one(m.DocumentProcessingJob, document_id=str(document_id)) if document else None
            if not document or not job:
                raise WorkflowError('找不到此 OCR 任务。', 404)
            if job.status != 'FAILED':
                raise WorkflowError('只有失败的 OCR 任务可以重试。')
            job.status, job.current_page, job.error_code = 'QUEUED', 0, None
            job.lease_expires_at, job.finished_at = None, None
            document.parse_status, document.parse_error = 'queued', None
            document.updated_at = m.now()
            return document_result(w, document)

    @app.get('/api/documents/{document_id}/download')
    def download_document(request: Request, document_id: UUID):
        with transaction(request) as w:
            document = w.one(m.UploadedDocument, id=str(document_id))
            if not document:
                raise WorkflowError('找不到此源文件。', 404)
            try:
                content = document_store.read(document.storage_key)
            except FileNotFoundError as exc:
                raise WorkflowError('源文件存储缺失，请联系管理员检查备份。', 404) from exc
            filename = quote(document.original_filename)
            return Response(content, media_type=document.media_type, headers={
                'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
                'X-Content-Type-Options': 'nosniff'})

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
            job, error = w.generate(session, str(target.round_id), str(target.section_id))
            state = w.get_current_state(session)
        # Provider failure is committed as history before returning an error response.
        if error:
            return JSONResponse(status_code=502, content={'detail': str(error), 'code': error.code})
        if job:
            return JSONResponse(status_code=202, content=jsonable_encoder(state))
        return state

    @app.post('/api/sessions/{session_id}/suggestions/{suggestion_id}/decision')
    def decide(request: Request, session_id: UUID, suggestion_id: UUID, data: Decide):
        with transaction(request) as w:
            session = w.session(str(session_id))
            candidate = w.decide(session, str(suggestion_id), data.decision, data.confirm_append,
                                 str(data.expected_version_id) if data.expected_version_id else None)
            return {**w.get_current_state(session), 'revision_candidate': candidate} if candidate else w.get_current_state(session)

    @app.post('/api/sessions/{session_id}/suggestions/{suggestion_id}/custom-prompt')
    def custom_prompt(request: Request, session_id: UUID, suggestion_id: UUID, data: CustomPromptRequest):
        with transaction(request) as w:
            session = w.session(str(session_id))
            job, error = w.customize(session, str(suggestion_id), data.prompt_text, str(data.request_key))
            state = w.get_current_state(session)
        if error:
            return JSONResponse(status_code=502, content={'detail': str(error), 'code': error.code})
        if job:
            return JSONResponse(status_code=202, content=jsonable_encoder(state))
        return state

    @app.post('/api/sessions/{session_id}/suggestions/{suggestion_id}/custom-prompt/retry')
    def retry_custom_prompt(request: Request, session_id: UUID, suggestion_id: UUID):
        with transaction(request) as w:
            session = w.session(str(session_id))
            w.retry_custom(session, str(suggestion_id))
            state = w.get_current_state(session)
        return JSONResponse(status_code=202, content=jsonable_encoder(state))

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

    frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if frontend.exists():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app


app = create_app()
