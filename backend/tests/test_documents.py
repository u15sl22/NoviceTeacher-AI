from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.api import create_app
from app.models import UploadedDocument
from app.storage import LocalDocumentStorage


def docx_bytes(*paragraphs):
    body = ''.join(
        '<w:p><w:r><w:t xml:space="preserve">' + value + '</w:t></w:r></w:p>'
        for value in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}</w:body></w:document>')
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    return output.getvalue()


def payload(document_id=None):
    return {'request_key': str(uuid4()), 'metadata': {
        'subject': '数学', 'grade': '三年级', 'topic': '分数'},
        'content': '教学目标\n理解分数。\n\n课堂练习\n完成练习。',
        'document_id': document_id}


def test_docx_upload_preview_attach_download_and_reuse_guard(environment, tmp_path):
    sessions, factory = environment
    storage = LocalDocumentStorage(tmp_path / 'documents')
    with TestClient(create_app(sessions, factory, document_storage=storage)) as client:
        source = docx_bytes('教学目标', '理解分数。', '课堂练习', '完成练习。')
        uploaded = client.post('/api/documents', params={'filename': '分数教案.docx'},
            content=source, headers={'Content-Type':
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document'})
        assert uploaded.status_code == 200, uploaded.text
        document = uploaded.json()
        assert document['text'] == '教学目标\n\n理解分数。\n\n课堂练习\n\n完成练习。'
        assert document['char_count'] == len(document['text'])
        assert storage.read(next(storage.root.iterdir()).name) == source

        created = client.post('/api/sessions', json=payload(document['id']))
        assert created.status_code == 200, created.text
        state = created.json()
        assert state['source_document']['id'] == document['id']
        assert state['source_document']['session_id'] == state['session']['id']

        download = client.get(f"/api/documents/{document['id']}/download")
        assert download.status_code == 200
        assert download.content == source
        assert "filename*=UTF-8''" in download.headers['content-disposition']

        reused = client.post('/api/sessions', json=payload(document['id']))
        assert reused.status_code == 409
        with sessions() as db:
            saved = db.scalar(select(UploadedDocument).where(UploadedDocument.id == document['id']))
            assert saved.sha256 == document['sha256']
            assert saved.user_id


def test_upload_rejects_invalid_docx_and_textless_pdf(environment, tmp_path):
    sessions, factory = environment
    with TestClient(create_app(sessions, factory,
            document_storage=LocalDocumentStorage(tmp_path / 'documents'))) as client:
        invalid = client.post('/api/documents', params={'filename': 'lesson.docx'}, content=b'not a zip')
        assert invalid.status_code == 422
        assert 'DOCX' in invalid.json()['detail']

        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.write(output)
        scanned = client.post('/api/documents', params={'filename': 'scan.pdf'}, content=output.getvalue())
        assert scanned.status_code == 422
        assert 'OCR' in scanned.json()['detail']
        assert not (tmp_path / 'documents').exists()


def test_upload_rejects_unsupported_extension(environment, tmp_path):
    sessions, factory = environment
    with TestClient(create_app(sessions, factory,
            document_storage=LocalDocumentStorage(tmp_path / 'documents'))) as client:
        response = client.post('/api/documents', params={'filename': 'lesson.doc'}, content=b'legacy word')
        assert response.status_code == 422
        assert '.docx' in response.json()['detail']
