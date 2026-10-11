"""Local PDF OCR adapter. No document content leaves the server."""
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .documents import DocumentParseError, ParsedDocument, _finish, pdf_page_texts


class OCRError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TesseractPDFOCR:
    parser_name = 'tesseract-chi-sim-eng-v1'

    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def available():
        return bool(shutil.which('pdftoppm') and shutil.which('tesseract'))

    def extract(self, content: bytes, progress=None) -> ParsedDocument:
        if not self.available():
            raise OCRError('OCR_ENGINE_UNAVAILABLE', '服务器尚未安装 PDF OCR 引擎。')
        try:
            existing = pdf_page_texts(content)
        except DocumentParseError as exc:
            raise OCRError('INVALID_PDF', str(exc)) from exc
        total = len(existing)
        if total > self.settings.ocr_max_pages:
            raise OCRError('OCR_PAGE_LIMIT', f'扫描 PDF 超过 OCR 上限 {self.settings.ocr_max_pages} 页。')
        results = []
        with TemporaryDirectory(prefix='pedagoloop-ocr-') as directory:
            source = Path(directory) / 'source.pdf'
            source.write_bytes(content)
            for index, extracted in enumerate(existing, 1):
                compact = ''.join(extracted.split())
                if len(compact) >= self.settings.ocr_min_text_chars_per_page:
                    text = extracted
                else:
                    prefix = Path(directory) / f'page-{index}'
                    try:
                        subprocess.run(['pdftoppm', '-f', str(index), '-l', str(index), '-singlefile',
                            '-r', str(self.settings.ocr_dpi), '-png', str(source), str(prefix)],
                            check=True, capture_output=True, timeout=self.settings.ocr_page_timeout_seconds)
                        image = prefix.with_suffix('.png')
                        completed = subprocess.run(['tesseract', str(image), 'stdout', '-l',
                            self.settings.ocr_languages, '--psm', '6'], check=True, capture_output=True,
                            timeout=self.settings.ocr_page_timeout_seconds)
                        text = completed.stdout.decode('utf-8', errors='replace').strip()
                    except subprocess.TimeoutExpired as exc:
                        raise OCRError('OCR_TIMEOUT', f'第 {index} 页识别超时。') from exc
                    except subprocess.CalledProcessError as exc:
                        detail = exc.stderr.decode('utf-8', errors='replace')[-500:] if exc.stderr else ''
                        raise OCRError('OCR_PROCESS_FAILED', f'第 {index} 页识别失败。{detail}') from exc
                results.append(text)
                if progress:
                    progress(index, total)
        try:
            text = _finish('\n\n'.join(results))
        except DocumentParseError as exc:
            raise OCRError('OCR_EMPTY_OR_TOO_LONG', str(exc)) from exc
        return ParsedDocument(text, 'application/pdf', self.parser_name, total)
