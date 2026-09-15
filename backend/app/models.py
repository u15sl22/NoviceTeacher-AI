"""Append-only history records; mutable projections live on Session/Section/LessonPlan."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


json_type = JSON().with_variant(JSONB(), 'postgresql')


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Participant(Record, Base):
    __tablename__ = 'participants'


class Session(Record, Base):
    __tablename__ = 'sessions'
    __table_args__ = (CheckConstraint("status IN ('CREATED','ACTIVE','ROUND_COMPLETED','TERMINATED')"),)
    participant_id: Mapped[str] = mapped_column(ForeignKey('participants.id'))
    request_key: Mapped[str] = mapped_column(String(36), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='CREATED')
    current_round_number: Mapped[int] = mapped_column(Integer, default=0)
    current_section_id: Mapped[str | None] = mapped_column(String(36))
    experiment_condition: Mapped[str | None] = mapped_column(String(100))
    config_snapshot: Mapped[dict] = mapped_column(json_type)
    lesson_metadata: Mapped[dict] = mapped_column(json_type)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LessonPlan(Record, Base):
    __tablename__ = 'lesson_plans'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), unique=True)
    original_content: Mapped[str] = mapped_column(Text)
    current_content: Mapped[str] = mapped_column(Text)


class LessonPlanVersion(Record, Base):
    __tablename__ = 'lesson_plan_versions'
    __table_args__ = (UniqueConstraint('lesson_plan_id', 'round_number'),)
    lesson_plan_id: Mapped[str] = mapped_column(ForeignKey('lesson_plans.id'))
    round_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class Round(Record, Base):
    __tablename__ = 'rounds'
    __table_args__ = (UniqueConstraint('session_id', 'round_number'),
                     CheckConstraint("status IN ('ACTIVE','COMPLETED')"),
                     CheckConstraint('round_number BETWEEN 1 AND 5'))
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'))
    round_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Section(Record, Base):
    __tablename__ = 'sections'
    __table_args__ = (UniqueConstraint('lesson_plan_id', 'order_index'),)
    lesson_plan_id: Mapped[str] = mapped_column(ForeignKey('lesson_plans.id'))
    title: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer)
    section_type: Mapped[str | None] = mapped_column(String(60))
    parent_section_id: Mapped[str | None] = mapped_column(ForeignKey('sections.id'))
    current_content: Mapped[str] = mapped_column(Text)


class SectionVersion(Record, Base):
    __tablename__ = 'section_versions'
    __table_args__ = (UniqueConstraint('section_id', 'version_number'),)
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    round_number: Mapped[int] = mapped_column(Integer)
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    previous_version_id: Mapped[str | None] = mapped_column(ForeignKey('section_versions.id'))


class SectionReview(Record, Base):
    """Durable per-round progress, including generated-but-empty suggestions."""
    __tablename__ = 'section_reviews'
    __table_args__ = (UniqueConstraint('round_id', 'section_id'),)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationRecord(Record, Base):
    __tablename__ = 'generation_records'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    section_version_id: Mapped[str] = mapped_column(ForeignKey('section_versions.id'))
    provider_type: Mapped[str] = mapped_column(String(100))
    runtime_context: Mapped[dict] = mapped_column(json_type)
    raw_response: Mapped[dict | None] = mapped_column(json_type)
    error_code: Mapped[str | None] = mapped_column(String(100))


class Suggestion(Record, Base):
    __tablename__ = 'suggestions'
    __table_args__ = (UniqueConstraint('round_id', 'section_id', 'suggestion_index'),
                     CheckConstraint('suggestion_index BETWEEN 1 AND 2'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    generation_id: Mapped[str] = mapped_column(ForeignKey('generation_records.id'))
    suggestion_index: Mapped[int] = mapped_column(Integer)
    issue: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    pedagogical_basis: Mapped[str] = mapped_column(Text)
    basis_type: Mapped[str] = mapped_column(String(40), default='provisional_model')
    revision: Mapped[str] = mapped_column(Text)
    provider_type: Mapped[str] = mapped_column(String(100))


class Decision(Record, Base):
    __tablename__ = 'decisions'
    __table_args__ = (CheckConstraint("decision IN ('ACCEPT','REJECT')"),)
    suggestion_id: Mapped[str] = mapped_column(ForeignKey('suggestions.id'), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    decision: Mapped[str] = mapped_column(String(10))


class CustomPrompt(Record, Base):
    __tablename__ = 'custom_prompts'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    prompt_text: Mapped[str] = mapped_column(Text)


class RetrievalRecord(Record, Base):
    __tablename__ = 'retrieval_records'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    items: Mapped[list] = mapped_column(json_type)


class InteractionEvent(Record, Base):
    __tablename__ = 'interaction_events'
    __table_args__ = (UniqueConstraint('session_id', 'sequence'),)
    participant_id: Mapped[str] = mapped_column(ForeignKey('participants.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str | None] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str | None] = mapped_column(ForeignKey('sections.id'))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(60))
    event_payload: Mapped[dict] = mapped_column(json_type)
