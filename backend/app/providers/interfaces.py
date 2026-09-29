"""Research contracts: providers receive plain snapshots, never a live DB session."""
from typing import Protocol, Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class SectionDraft(BaseModel):
    title: str
    content: str
    section_type: str | None = None


class KnowledgeItem(BaseModel):
    id: str
    content: str
    source: str
    metadata: dict = Field(default_factory=dict)
    score: float | None = None


class SuggestionDraft(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    issue: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)
    pedagogical_basis: str = Field(min_length=1, max_length=4000)
    revision: str = Field(min_length=1, max_length=8000)
    issue_type: Literal['missing_component', 'objective_measurability', 'learner_analysis',
        'content_accuracy', 'pedagogical_alignment', 'activity_design', 'assessment_alignment',
        'interaction_quality', 'cross_section_consistency', 'resource_design', 'document_quality', 'other'] = 'other'
    scope: Literal['local', 'cross_section', 'global'] = 'local'
    confidence: float | None = Field(default=None, ge=0, le=1)
    revision_mode: Literal['append', 'replace'] = 'append'
    target_text: str | None = None
    knowledge_ids: list[str] = Field(default_factory=list)


class MemoryContext(BaseModel):
    latest_content: str
    rejected_suggestions: list[dict] = Field(default_factory=list)
    accepted_suggestions: list[dict] = Field(default_factory=list)


class LLMContext(BaseModel):
    system: str
    user: dict[str, Any]


class SectionParser(Protocol):
    def parse(self, lesson_plan_text: str, lesson_metadata: dict | None = None) -> list[SectionDraft]: ...


class SuggestionProvider(Protocol):
    def generate(self, context: LLMContext) -> list[SuggestionDraft]: ...


class KnowledgeRetriever(Protocol):
    def retrieve(self, section: dict, lesson_metadata: dict, query: str | None = None,
                 limit: int | None = None) -> list[KnowledgeItem]: ...


class MemoryProvider(Protocol):
    def build_memory(self, session: dict, round: dict, section: dict) -> MemoryContext: ...


class ContextBuilder(Protocol):
    def build(self, section: dict, lesson_metadata: dict, memory_context: MemoryContext,
              retrieved_knowledge: list[KnowledgeItem], custom_prompt: str | None = None) -> LLMContext: ...


class LLMClient(Protocol):
    def complete(self, context: LLMContext) -> dict: ...


class InquiryProvider(Protocol):
    def inquire(self, session: dict, section: dict, prompt_text: str, context: LLMContext) -> list[SuggestionDraft]: ...


class RevisionStrategy(Protocol):
    def apply(self, section_content: str, suggestion: SuggestionDraft) -> 'RevisionResult': ...


class RevisionResult(BaseModel):
    status: Literal['applied', 'candidate']
    content: str
    candidate: str | None = None
    reason: str | None = None
