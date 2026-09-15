from dataclasses import dataclass
from .interfaces import (SectionParser, SuggestionProvider, KnowledgeRetriever, MemoryProvider,
                         ContextBuilder, InquiryProvider, RevisionStrategy)
from .defaults import (DefaultSectionParser, MockSuggestionProvider, DummyKnowledgeRetriever,
                       DefaultMemoryProvider, DefaultContextBuilder, DisabledInquiryProvider, DefaultRevisionStrategy)
from .llm import GenericLLMSuggestionProvider, CompatibleLLMClient


@dataclass(frozen=True)
class ExperimentConfig:
    section_parser: SectionParser
    suggestion_provider: SuggestionProvider
    knowledge_retriever: KnowledgeRetriever
    memory_provider: MemoryProvider
    context_builder: ContextBuilder
    inquiry_provider: InquiryProvider
    revision_strategy: RevisionStrategy
    max_rounds: int = 5
    enable_active_inquiry: bool = False


class ExperimentConfigFactory:
    def __init__(self, settings):
        self.settings = settings

    def snapshot(self):
        if self.settings.suggestion_provider not in ('mock', 'generic_llm'):
            raise ValueError('SUGGESTION_PROVIDER must be mock or generic_llm')
        if set(self.settings.llm_extra_body) - {'thinking', 'max_tokens', 'temperature', 'top_p', 'reasoning_effort'}:
            raise ValueError('LLM_EXTRA_BODY contains unsupported generation parameters')
        return {'name': 'DEFAULT_DEMO_CONFIG', 'version': 1, 'provider': self.settings.suggestion_provider,
                'model': self.settings.llm_model, 'base_url': self.settings.llm_base_url,
                'extra_body': self.settings.llm_extra_body,
                'max_rounds': 5, 'enable_active_inquiry': False,
                'section_parser': 'default_v1', 'memory': 'reject_only_v1',
                'retrieval': 'dummy_v1', 'context': 'default_v1', 'revision': 'append_v1'}

    def build(self, snapshot, history_reader):
        # Version the registry when strategies change; never silently reinterpret old sessions.
        expected = {'version': 1, 'section_parser': 'default_v1', 'memory': 'reject_only_v1',
                    'retrieval': 'dummy_v1', 'context': 'default_v1', 'revision': 'append_v1',
                    'max_rounds': 5, 'enable_active_inquiry': False}
        if any(snapshot.get(k) != v for k, v in expected.items()):
            raise ValueError('Unsupported saved experiment configuration')
        providers = {
            'mock': lambda: MockSuggestionProvider(),
            'generic_llm': lambda: GenericLLMSuggestionProvider(CompatibleLLMClient(
                snapshot['base_url'], snapshot['model'], self.settings.llm_api_key, self.settings.llm_timeout_seconds,
                extra_body=snapshot.get('extra_body', {}))),
        }
        return ExperimentConfig(DefaultSectionParser(), providers[snapshot['provider']](),
            DummyKnowledgeRetriever(), DefaultMemoryProvider(history_reader), DefaultContextBuilder(),
            DisabledInquiryProvider(), DefaultRevisionStrategy(), snapshot['max_rounds'])
