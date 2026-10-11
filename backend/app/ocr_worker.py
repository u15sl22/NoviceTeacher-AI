"""Durable worker for local scanned-PDF OCR."""
import argparse
import time
from datetime import timedelta

from sqlalchemy import or_, select

from . import models as m
from .db import SessionLocal
from .ocr import OCRError, TesseractPDFOCR
from .settings import settings
from .storage import LocalDocumentStorage


class OCRWorker:
    def __init__(self, session_factory=SessionLocal, document_storage=None, engine=None,
                 lease_seconds=3600):
        self.session_factory = session_factory
        self.storage = document_storage or LocalDocumentStorage(settings.storage_root)
        self.engine = engine or TesseractPDFOCR(settings)
        self.lease_seconds = lease_seconds

    def claim(self):
        now = m.now()
        with self.session_factory.begin() as db:
            statement = select(m.DocumentProcessingJob).where(or_(
                m.DocumentProcessingJob.status == 'QUEUED',
                (m.DocumentProcessingJob.status == 'RUNNING') &
                (m.DocumentProcessingJob.lease_expires_at < now),
            )).order_by(m.DocumentProcessingJob.created_at, m.DocumentProcessingJob.id).limit(1)
            if db.bind.dialect.name == 'postgresql':
                statement = statement.with_for_update(skip_locked=True)
            else:
                statement = statement.with_for_update()
            job = db.scalar(statement)
            if not job:
                return None
            document = db.get(m.UploadedDocument, job.document_id)
            job.status = 'RUNNING'
            job.attempt_count += 1
            job.started_at = job.started_at or now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            document.parse_status = 'processing'
            document.parse_error = None
            document.updated_at = now
            return job.id

    def _load(self, job_id):
        with self.session_factory() as db:
            job = db.get(m.DocumentProcessingJob, job_id)
            document = db.get(m.UploadedDocument, job.document_id) if job else None
            if not job or not document or job.status != 'RUNNING':
                return None
            return document.id, document.storage_key

    def _progress(self, job_id, current, total):
        with self.session_factory.begin() as db:
            job = db.get(m.DocumentProcessingJob, job_id)
            if job and job.status == 'RUNNING':
                job.current_page = current
                job.total_pages = total
                job.lease_expires_at = m.now() + timedelta(seconds=self.lease_seconds)

    def _finish(self, job_id, parsed=None, error=None):
        with self.session_factory.begin() as db:
            job = db.scalar(select(m.DocumentProcessingJob)
                .where(m.DocumentProcessingJob.id == job_id).with_for_update())
            if not job or job.status != 'RUNNING':
                return
            document = db.get(m.UploadedDocument, job.document_id)
            now = m.now()
            if error:
                job.status, job.error_code = 'FAILED', error.code
                document.parse_status, document.parse_error = 'failed', str(error)
            else:
                job.status = 'SUCCEEDED'
                job.current_page = parsed.page_count or job.current_page
                job.total_pages = parsed.page_count
                document.parse_status = 'parsed'
                document.parser_name = parsed.parser_name
                document.extracted_text = parsed.text
                document.page_count = parsed.page_count
                document.parse_error = None
            job.finished_at, job.lease_expires_at = now, None
            document.updated_at = now

    def retry(self, job):
        job.status = 'QUEUED'
        job.current_page = 0
        job.lease_expires_at = None
        job.finished_at = None
        job.error_code = None

    def run_once(self):
        job_id = self.claim()
        if not job_id:
            return False
        try:
            loaded = self._load(job_id)
            if not loaded:
                return True
            _, storage_key = loaded
            content = self.storage.read(storage_key)
            parsed = self.engine.extract(content,
                lambda current, total: self._progress(job_id, current, total))
            self._finish(job_id, parsed=parsed)
        except OCRError as error:
            self._finish(job_id, error=error)
        except Exception:
            self._finish(job_id, error=OCRError('OCR_WORKER_FAILURE',
                '后台 OCR 任务执行失败，可以重试。'))
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
    worker = OCRWorker()
    if args.once:
        worker.run_once()
    else:
        worker.run_forever(args.poll_seconds)


if __name__ == '__main__':
    main()
