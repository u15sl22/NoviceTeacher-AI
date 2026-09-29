"""Read-only, verified-only metadata retrieval. No embeddings at runtime."""
import re
from typing import Protocol
from pydantic import BaseModel, Field
from sqlalchemy import select
from . import models as m


class RetrievalQuery(BaseModel):
    subject: str
    grade: str
    topic: str
    section_type: str | None = None
    issue_type: str | None = None
    section_content: str
    top_k: int = Field(default=3, ge=1, le=10)


class RetrievalItem(BaseModel):
    id: str
    content: str
    source: str
    source_locator: str = ''
    score: float = 0
    metadata: dict = Field(default_factory=dict)


class Retriever(Protocol):
    def retrieve(self, query: RetrievalQuery) -> list[RetrievalItem]: ...


class EmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> list[float]: ...


class NoOpRetriever:
    def retrieve(self, query): return []


def normalize_subject(value):
    return {'小学数学': '数学', 'math': '数学', 'mathematics': '数学',
            '小学英语': '英语', 'english': '英语'}.get(value.strip().lower(), value.strip().lower())


def tokens(value):
    return set(re.findall(r'[a-z]+|[\u4e00-\u9fff]{2}', value.lower()))


class MetadataRetriever:
    def __init__(self, db, kind):
        self.db, self.kind = db, kind

    def retrieve(self, query):
        cls = {'case': m.CaseItem, 'knowledge': m.KnowledgeItem}[self.kind]
        statement = select(cls).where(cls.verification_status == 'verified')
        if self.kind == 'case':
            statement = statement.join(m.DatasetAnnotation, cls.source_annotation_id == m.DatasetAnnotation.id).where(
                m.DatasetAnnotation.verification_status == 'verified')
        ranked = []
        for item in self.db.scalars(statement):
            if normalize_subject(item.subject) != normalize_subject(query.subject):
                continue
            text = item.content if self.kind == 'knowledge' else item.original_text + '\n' + item.issue + '\n' + item.analysis + '\n' + item.suggestion
            score = len(tokens(query.topic + query.section_content) & tokens(item.topic + text))
            score += 5 * bool(item.section_type and item.section_type == query.section_type)
            score += 2 * bool(item.grade and (item.grade in query.grade or query.grade in item.grade))
            score += 4 * bool(query.topic and query.topic in item.topic)
            if score <= 0:
                continue
            ranked.append(RetrievalItem(id=item.id, content=text,
                source=item.source if self.kind == 'knowledge' else 'dataset_annotation:' + item.source_annotation_id,
                source_locator=item.source_locator if self.kind == 'knowledge' else item.source_annotation_id,
                score=float(score), metadata={'kind': self.kind, 'verification_status': 'verified', 'score_type': 'metadata_overlap'}))
        return sorted(ranked, key=lambda x: (-x.score, x.id))[:query.top_k]
