"""Lossless-enough text extraction for lesson-plan upload previews."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from pypdf import PdfReader


MAX_TEXT_CHARS = 100_000
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 300


class DocumentParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    media_type: str
    parser_name: str
    page_count: int | None = None


def _finish(text: str) -> str:
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    text = '\n'.join(lines).strip()
    if not text:
        raise DocumentParseError('文件中没有可提取的文字；扫描版 PDF 暂不支持，请先完成 OCR。')
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentParseError(f'提取后的正文超过 {MAX_TEXT_CHARS} 字，请精简后再上传。')
    return text


def _docx(content: bytes) -> ParsedDocument:
    if not content.startswith(b'PK'):
        raise DocumentParseError('文件内容不是有效的 DOCX。')
    try:
        with ZipFile(BytesIO(content)) as archive:
            try:
                info = archive.getinfo('word/document.xml')
            except KeyError as exc:
                raise DocumentParseError('DOCX 缺少正文结构。') from exc
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise DocumentParseError('DOCX 正文结构过大。')
            root = ElementTree.fromstring(archive.read(info))
    except (BadZipFile, ElementTree.ParseError) as exc:
        raise DocumentParseError('DOCX 文件损坏或格式无效。') from exc

    def text_of(node):
        parts = []
        for item in node.iter():
            name = item.tag.rsplit('}', 1)[-1]
            if name == 't' and item.text:
                parts.append(item.text)
            elif name == 'tab':
                parts.append('\t')
            elif name in ('br', 'cr'):
                parts.append('\n')
        return ''.join(parts).strip()

    body = next((node for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 'body'), None)
    blocks = []
    for child in list(body) if body is not None else []:
        name = child.tag.rsplit('}', 1)[-1]
        if name == 'p':
            if value := text_of(child):
                blocks.append(value)
        elif name == 'tbl':
            for row in (node for node in list(child) if node.tag.rsplit('}', 1)[-1] == 'tr'):
                cells = [text_of(cell) for cell in list(row) if cell.tag.rsplit('}', 1)[-1] == 'tc']
                if any(cells):
                    blocks.append('\t'.join(cells))
    return ParsedDocument(_finish('\n\n'.join(blocks)),
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx-xml-v1')


def _pdf(content: bytes) -> ParsedDocument:
    if not content.startswith(b'%PDF-'):
        raise DocumentParseError('文件内容不是有效的 PDF。')
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(''):
                    raise DocumentParseError('加密 PDF 暂不支持，请先移除密码。')
            except Exception as exc:
                raise DocumentParseError('加密 PDF 暂不支持，请先移除密码。') from exc
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentParseError(f'PDF 超过 {MAX_PDF_PAGES} 页。')
        pages = [(page.extract_text() or '').strip() for page in reader.pages]
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError('PDF 文件损坏或无法解析。') from exc
    return ParsedDocument(_finish('\n\n'.join(pages)), 'application/pdf', 'pypdf-6.19.0', len(reader.pages))


def parse_document(content: bytes, filename: str) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    if suffix == '.docx':
        return _docx(content)
    if suffix == '.pdf':
        return _pdf(content)
    raise DocumentParseError('仅支持 .docx 和 .pdf 文件。')
