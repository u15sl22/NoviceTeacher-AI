from dataclasses import dataclass
from .interfaces import SectionParser, SuggestionProvider, RevisionStrategy
from .defaults import DefaultSectionParser, DefaultMemoryProvider, DefaultContextBuilder
from .alpha import BoundedSectionParser, LegacyRevisionAdapter, ContextOnlyMockProvider
from .revision import ReplaceOrAppendRevisionStrategy, AppendRevisionStrategy
from .llm import GenericLLMSuggestionProvider, CompatibleLLMClient
from .registry import CapabilityRegistry
from ..context_engines import PipelineContextEngine, LegacyContextEngine
from ..contributors import (LessonContextContributor, RuleBasedContextPolicy, SessionMemoryContributor,
    RuleBasedMemoryProvider, RejectOnlyMemoryProvider, NoMemoryProvider, CaseRetrievalContributor,
    KnowledgeRetrievalContributor, PreferenceContributor, NoPreferenceProvider)
from ..retrieval import MetadataRetriever, NoOpRetriever


@dataclass(frozen=True)
class ExperimentConfig:
    section_parser: SectionParser
    suggestion_provider: SuggestionProvider
    revision_strategy: RevisionStrategy
    context_engine: object
    max_rounds: int = 5
    enable_active_inquiry: bool = False


def default_registry():
    registry = CapabilityRegistry()
    registry.register('bounded_v2', lambda **kwargs: BoundedSectionParser())
    registry.register('replace_or_append_v2', lambda **kwargs: ReplaceOrAppendRevisionStrategy())
    registry.register('append_v2', lambda **kwargs: AppendRevisionStrategy())
    registry.register('lesson_context', lambda history, snapshot: LessonContextContributor(RuleBasedContextPolicy()))
    memories = {'rule_based': RuleBasedMemoryProvider, 'reject_only': RejectOnlyMemoryProvider,
                'none': lambda history: NoMemoryProvider()}
    registry.register('session_memory', lambda history, snapshot: SessionMemoryContributor(memories[snapshot['memory']](history)))
    retrievers = {'metadata': lambda db, kind: MetadataRetriever(db, kind), 'none': lambda db, kind: NoOpRetriever()}
    registry.register('case_retrieval', lambda history, snapshot: CaseRetrievalContributor(
        retrievers[snapshot['case_retriever']](history.db, 'case'), snapshot['retrieval_top_k']))
    registry.register('knowledge_retrieval', lambda history, snapshot: KnowledgeRetrievalContributor(
        retrievers[snapshot['knowledge_retriever']](history.db, 'knowledge'), snapshot['retrieval_top_k']))
    registry.register('preferences', lambda history, snapshot: PreferenceContributor(NoPreferenceProvider()))
    return registry


class ExperimentConfigFactory:
    def __init__(self, settings, registry=None):
        self.settings, self.registry = settings, registry or default_registry()

    def snapshot(self):
        cfg = self.settings
        if cfg.suggestion_provider not in ('mock', 'generic_llm'): raise ValueError('Unknown suggestion provider')
        if set(cfg.llm_extra_body) - {'thinking', 'max_tokens', 'temperature', 'top_p', 'reasoning_effort'}:
            raise ValueError('Unsupported model generation parameters')
        if len(cfg.context_contributors) != len(set(cfg.context_contributors)):
            raise ValueError('Duplicate contributors')
        for name in cfg.context_contributors:
            if name not in self.registry.factories: raise ValueError('Unknown contributor: ' + name)
        return {'name': 'ALPHA_CONFIG', 'version': 2, 'provider': cfg.suggestion_provider,
            'model_provider': cfg.llm_provider, 'model': cfg.llm_model, 'base_url': cfg.llm_base_url,
            'extra_body': cfg.llm_extra_body, 'timeout': cfg.llm_timeout_seconds,
            'context_contributors': list(cfg.context_contributors), 'max_context_tokens': cfg.max_context_tokens,
            'section_parser': cfg.section_parser, 'revision': cfg.revision_strategy, 'memory': cfg.memory_provider,
            'case_retriever': cfg.case_retriever, 'knowledge_retriever': cfg.knowledge_retriever,
            'retrieval_top_k': cfg.retrieval_top_k, 'auth_provider': cfg.auth_provider,
            'max_rounds': 5, 'enable_active_inquiry': False, 'prompt_version': 'alpha_v2'}

    def build(self, snapshot, history_reader):
        providers = {'mock': lambda: ContextOnlyMockProvider(),
            'generic_llm': lambda: GenericLLMSuggestionProvider(CompatibleLLMClient(snapshot['base_url'],
                snapshot['model'], self.settings.llm_api_key, snapshot.get('timeout', self.settings.llm_timeout_seconds),
                extra_body=snapshot.get('extra_body', {})))}
        provider = providers[snapshot['provider']]()
        if snapshot['version'] == 1:
            return ExperimentConfig(DefaultSectionParser(), provider, LegacyRevisionAdapter(),
                LegacyContextEngine(DefaultMemoryProvider(history_reader), DefaultContextBuilder()))
        if snapshot['version'] != 2:
            raise ValueError('Unsupported saved configuration')
        contributors = [self.registry.create(name, history=history_reader, snapshot=snapshot)
                        for name in snapshot['context_contributors']]
        return ExperimentConfig(self.registry.create(snapshot['section_parser']), provider, self.registry.create(snapshot['revision']),
            PipelineContextEngine(contributors, snapshot['max_context_tokens']), snapshot['max_rounds'])
