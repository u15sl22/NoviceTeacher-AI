from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


class LessonMetadata(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=2000)
    textbook: str | None = Field(default=None, max_length=500)
    additional_context: str | None = Field(default=None, max_length=4000)


class CreateSession(BaseModel):
    request_key: UUID
    metadata: LessonMetadata
    content: str = Field(min_length=1, max_length=100000)


class Decide(BaseModel):
    decision: Literal['ACCEPT', 'REJECT']


class ReviewTarget(BaseModel):
    round_id: UUID
    section_id: UUID


class ViewAck(ReviewTarget):
    section_version_id: UUID
    suggestion_ids: list[UUID] = Field(default_factory=list, max_length=2)
    decision_ids: list[UUID] = Field(default_factory=list, max_length=2)
