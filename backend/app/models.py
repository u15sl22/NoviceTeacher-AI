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


class Owned:
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)


class User(Record, Base):
    __tablename__ = 'users'
    username: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(30), default='user')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Participant(Owned, Record, Base):
    __tablename__ = 'participants'


class Session(Owned, Record, Base):
    __tablename__ = 'sessions'
    __table_args__ = (CheckConstraint("status IN ('CREATED','ACTIVE','ROUND_COMPLETED','TERMINATED')"), UniqueConstraint('user_id', 'request_key'))
    participant_id: Mapped[str] = mapped_column(ForeignKey('participants.id'))
    request_key: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='CREATED')
    current_round_number: Mapped[int] = mapped_column(Integer, default=0)
    current_section_id: Mapped[str | None] = mapped_column(String(36))
    experiment_condition: Mapped[str | None] = mapped_column(String(100))
    config_snapshot: Mapped[dict] = mapped_column(json_type)
    lesson_metadata: Mapped[dict] = mapped_column(json_type)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LessonPlan(Owned, Record, Base):
    __tablename__ = 'lesson_plans'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), unique=True)
    original_content: Mapped[str] = mapped_column(Text)
    current_content: Mapped[str] = mapped_column(Text)


class LessonPlanVersion(Owned, Record, Base):
    __tablename__ = 'lesson_plan_versions'
    __table_args__ = (UniqueConstraint('lesson_plan_id', 'round_number'),)
    lesson_plan_id: Mapped[str] = mapped_column(ForeignKey('lesson_plans.id'))
    round_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class Round(Owned, Record, Base):
    __tablename__ = 'rounds'
    __table_args__ = (UniqueConstraint('session_id', 'round_number'),
                     CheckConstraint("status IN ('ACTIVE','COMPLETED')"),
                     CheckConstraint('round_number BETWEEN 1 AND 5'))
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'))
    round_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Section(Owned, Record, Base):
    __tablename__ = 'sections'
    __table_args__ = (UniqueConstraint('lesson_plan_id', 'order_index'),)
    lesson_plan_id: Mapped[str] = mapped_column(ForeignKey('lesson_plans.id'))
    title: Mapped[str] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer)
    section_type: Mapped[str | None] = mapped_column(String(60))
    parent_section_id: Mapped[str | None] = mapped_column(ForeignKey('sections.id'))
    current_content: Mapped[str] = mapped_column(Text)


class SectionVersion(Owned, Record, Base):
    __tablename__ = 'section_versions'
    __table_args__ = (UniqueConstraint('section_id', 'version_number'),)
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    round_number: Mapped[int] = mapped_column(Integer)
    version_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    previous_version_id: Mapped[str | None] = mapped_column(ForeignKey('section_versions.id'))


class SectionReview(Owned, Record, Base):
    """Durable per-round progress, including generated-but-empty suggestions."""
    __tablename__ = 'section_reviews'
    __table_args__ = (UniqueConstraint('round_id', 'section_id'),)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationRecord(Owned, Record, Base):
    __tablename__ = 'generation_records'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    section_version_id: Mapped[str] = mapped_column(ForeignKey('section_versions.id'))
    provider_type: Mapped[str] = mapped_column(String(100))
    runtime_context: Mapped[dict] = mapped_column(json_type)
    raw_response: Mapped[dict | None] = mapped_column(json_type)
    error_code: Mapped[str | None] = mapped_column(String(100))


class Suggestion(Owned, Record, Base):
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
    issue_type: Mapped[str] = mapped_column(String(50), default='other')
    scope: Mapped[str] = mapped_column(String(30), default='local')
    confidence: Mapped[float | None]
    target_text: Mapped[str | None] = mapped_column(Text)
    revision_mode: Mapped[str] = mapped_column(String(20), default='append')
    knowledge_ids: Mapped[list] = mapped_column(json_type, default=list)
    basis_sources: Mapped[list] = mapped_column(json_type, default=list)


class Decision(Owned, Record, Base):
    __tablename__ = 'decisions'
    __table_args__ = (CheckConstraint("decision IN ('ACCEPT','REJECT')"),)
    suggestion_id: Mapped[str] = mapped_column(ForeignKey('suggestions.id'), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    decision: Mapped[str] = mapped_column(String(10))


class CustomPrompt(Owned, Record, Base):
    __tablename__ = 'custom_prompts'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    prompt_text: Mapped[str] = mapped_column(Text)


class RetrievalRecord(Owned, Record, Base):
    __tablename__ = 'retrieval_records'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    items: Mapped[list] = mapped_column(json_type)
    contributor_type: Mapped[str] = mapped_column(String(60), default='legacy')
    query_text: Mapped[str] = mapped_column(Text, default='')
    filters: Mapped[dict] = mapped_column(json_type, default=dict)
    retriever_type: Mapped[str] = mapped_column(String(60), default='dummy_v1')
    retrieved_item_ids: Mapped[list] = mapped_column(json_type, default=list)
    similarity_scores: Mapped[list] = mapped_column(json_type, default=list)
    top_k: Mapped[int] = mapped_column(Integer, default=0)


class SystemConfigSnapshot(Owned, Record, Base):
    __tablename__ = 'system_config_snapshots'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), unique=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(json_type)


class LessonOverview(Owned, Record, Base):
    __tablename__ = 'lesson_overviews'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), unique=True)
    content: Mapped[dict] = mapped_column(json_type)


class Conversation(Owned, Record, Base):
    __tablename__ = 'conversations'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), unique=True)


class ChatMessage(Owned, Record, Base):
    __tablename__ = 'chat_messages'
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(40))
    related_section_id: Mapped[str | None] = mapped_column(ForeignKey('sections.id'))
    related_suggestion_id: Mapped[str | None] = mapped_column(ForeignKey('suggestions.id'))


class UserPreference(Owned, Record, Base):
    __tablename__ = 'user_preferences'
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[dict] = mapped_column(json_type)


class ContextSnapshot(Owned, Record, Base):
    __tablename__ = 'context_snapshots'
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str] = mapped_column(ForeignKey('sections.id'))
    generation_id: Mapped[str] = mapped_column(ForeignKey('generation_records.id'), unique=True)
    current_section_version_id: Mapped[str] = mapped_column(ForeignKey('section_versions.id'))
    contributor_names: Mapped[list] = mapped_column(json_type)
    fragment_source_ids: Mapped[dict] = mapped_column(json_type)
    lesson_overview_id: Mapped[str | None] = mapped_column(ForeignKey('lesson_overviews.id'))
    related_section_ids: Mapped[list] = mapped_column(json_type)
    memory_reference_ids: Mapped[list] = mapped_column(json_type)
    preference_ids: Mapped[list] = mapped_column(json_type)
    knowledge_ids: Mapped[list] = mapped_column(json_type)
    case_ids: Mapped[list] = mapped_column(json_type)
    prompt_version: Mapped[str] = mapped_column(String(60))
    model_provider: Mapped[str] = mapped_column(String(60))
    model_name: Mapped[str] = mapped_column(String(100))
    system_config_version: Mapped[int] = mapped_column(Integer)
    context_json: Mapped[dict] = mapped_column(json_type)


class DatasetItem(Record, Base):
    __tablename__ = 'dataset_items'
    import_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_document_name: Mapped[str] = mapped_column(Text)
    source_reference: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String(100))
    original_grade_label: Mapped[str] = mapped_column(String(200))
    normalized_grade: Mapped[str | None] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(Text)
    file_format: Mapped[str] = mapped_column(String(20))
    raw_metadata: Mapped[dict] = mapped_column(json_type)
    source_group: Mapped[str] = mapped_column(String(100))


class DatasetAnnotation(Record, Base):
    __tablename__ = 'dataset_annotations'
    import_key: Mapped[str] = mapped_column(String(64), unique=True)
    dataset_item_id: Mapped[str] = mapped_column(ForeignKey('dataset_items.id'))
    source_dimension: Mapped[str] = mapped_column(Text)
    source_location: Mapped[str] = mapped_column(Text)
    quoted_text: Mapped[str] = mapped_column(Text)
    evaluation: Mapped[str] = mapped_column(Text)
    analysis: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    detailed_suggestion: Mapped[str] = mapped_column(Text)
    source_basis: Mapped[str] = mapped_column(Text)
    theoretical_basis: Mapped[str] = mapped_column(Text)
    teaching_method_reference: Mapped[str] = mapped_column(Text)
    source_label: Mapped[str] = mapped_column(Text)
    raw_payload: Mapped[dict] = mapped_column(json_type)
    verification_status: Mapped[str] = mapped_column(String(20), default='raw')
    __table_args__ = (CheckConstraint("verification_status IN ('raw','reviewed','verified','rejected')"),)


class DatasetVerification(Record, Base):
    __tablename__ = 'dataset_verifications'
    annotation_id: Mapped[str] = mapped_column(ForeignKey('dataset_annotations.id'))
    reviewer_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'))
    issue_exists: Mapped[bool]
    location_correct: Mapped[bool]
    suggestion_actionable: Mapped[bool]
    basis_supported: Mapped[bool]
    worth_fixing: Mapped[bool]
    notes: Mapped[str] = mapped_column(Text)


class RetrievalAsset:
    subject: Mapped[str] = mapped_column(String(100))
    grade: Mapped[str | None] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(Text)
    section_type: Mapped[str | None] = mapped_column(String(60))
    verification_status: Mapped[str] = mapped_column(String(20), default='raw')
    asset_metadata: Mapped[dict] = mapped_column(json_type, default=dict)
    embedding: Mapped[list | None] = mapped_column(json_type)


class CaseItem(RetrievalAsset, Record, Base):
    __tablename__ = 'case_items'
    source_dataset_item_id: Mapped[str] = mapped_column(ForeignKey('dataset_items.id'))
    source_annotation_id: Mapped[str] = mapped_column(ForeignKey('dataset_annotations.id'), unique=True)
    issue_type: Mapped[str] = mapped_column(String(60), default='other')
    original_text: Mapped[str] = mapped_column(Text)
    issue: Mapped[str] = mapped_column(Text)
    analysis: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)


class KnowledgeItem(RetrievalAsset, Record, Base):
    __tablename__ = 'knowledge_items'
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(100))
    source_locator: Mapped[str] = mapped_column(Text)
    pedagogical_dimension: Mapped[str | None] = mapped_column(String(100))


class InteractionEvent(Owned, Record, Base):
    __tablename__ = 'interaction_events'
    __table_args__ = (UniqueConstraint('session_id', 'sequence'),)
    participant_id: Mapped[str] = mapped_column(ForeignKey('participants.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'), index=True)
    round_id: Mapped[str | None] = mapped_column(ForeignKey('rounds.id'))
    section_id: Mapped[str | None] = mapped_column(ForeignKey('sections.id'))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(60))
    event_payload: Mapped[dict] = mapped_column(json_type)
