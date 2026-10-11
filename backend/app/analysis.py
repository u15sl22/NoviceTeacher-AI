"""Deterministic lesson analysis planning and UTF-8 byte bounded chunking."""
from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class AnalysisChunk:
    index: int
    start_offset: int
    end_offset: int
    content: str
    content_sha256: str
    estimated_bytes: int
    overlap_before: int = 0

    def as_dict(self):
        return self.__dict__.copy()


def utf8_bytes(value: str) -> int:
    return len(value.encode('utf-8'))


def _largest_prefix(text: str, byte_limit: int) -> int:
    low, high = 1, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if utf8_bytes(text[:middle]) <= byte_limit:
            low = middle
        else:
            high = middle - 1
    return low


def split_text(text: str, byte_limit: int, overlap_chars: int = 120) -> list[AnalysisChunk]:
    if byte_limit < 512:
        raise ValueError('分析块预算过小，至少需要 512 字节。')
    if not text:
        return []
    chunks, cursor, previous_end = [], 0, 0
    boundaries = ('\n\n', '\n', '。', '！', '？', '；', '. ', '! ', '? ', '; ')
    while cursor < len(text):
        remaining = text[cursor:]
        if utf8_bytes(remaining) <= byte_limit:
            end = len(text)
        else:
            length = _largest_prefix(remaining, byte_limit)
            floor = max(1, int(length * 0.55))
            candidate = remaining[:length]
            cut = -1
            for marker in boundaries:
                position = candidate.rfind(marker, floor)
                if position >= 0:
                    cut = max(cut, position + len(marker))
            end = cursor + (cut if cut > 0 else length)
        content = text[cursor:end]
        overlap = max(0, previous_end - cursor)
        chunks.append(AnalysisChunk(len(chunks), cursor, end, content,
            hashlib.sha256(content.encode('utf-8')).hexdigest(), utf8_bytes(content), overlap))
        if end >= len(text):
            break
        # Keep overlap useful but bounded relative to small byte budgets. A
        # fixed 120-character overlap on a ~170-character Chinese chunk would
        # multiply calls without adding comparable context.
        desired_overlap = min(overlap_chars, max(1, len(content) // 10))
        previous_end = end
        next_cursor = max(end - desired_overlap, cursor + 1)
        cursor = next_cursor
    return chunks


def content_budget(max_context_bytes: int, system_prompt: str) -> int:
    # Reserve deterministic room for JSON structure, metadata and model instructions.
    return max(512, max_context_bytes - utf8_bytes(system_prompt) - 2048)


def build_analysis_plan(drafts, max_context_bytes: int, system_prompt: str,
                        max_chunks_per_section: int = 24, max_chunks_per_lesson: int = 80):
    limit = content_budget(max_context_bytes, system_prompt)
    sections, total = [], 0
    warnings = []
    for index, draft in enumerate(drafts):
        chunks = split_text(draft.content, limit)
        total += len(chunks)
        sections.append({'section_index': index, 'title': draft.title,
            'content_bytes': utf8_bytes(draft.content), 'chunk_count': len(chunks),
            'largest_chunk_bytes': max((c.estimated_bytes for c in chunks), default=0)})
        if len(chunks) > 1:
            warnings.append(f'“{draft.title}”将拆为 {len(chunks)} 个内部分析块；教师界面仍显示为一个单元。')
    processable = all(item['chunk_count'] <= max_chunks_per_section for item in sections) and total <= max_chunks_per_lesson
    if not processable:
        warnings.append('内部分析块数量超过安全上限，请缩短教案或增加可审阅标题。')
    return {'processable': processable, 'context_budget_bytes': max_context_bytes,
        'analysis_chunk_bytes': limit, 'section_count': len(sections),
        'analysis_chunk_count': total, 'estimated_model_calls': total,
        'requires_confirmation': total > 10, 'sections': sections, 'warnings': warnings}
