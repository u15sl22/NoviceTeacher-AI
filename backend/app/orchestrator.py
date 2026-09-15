from .providers.interfaces import SuggestionDraft
from .providers.llm import ProviderError


class ReviewOrchestrator:
    """Only coordinates injected contracts; the audit sink persists each stage."""
    def __init__(self, config):
        self.config = config

    def generate(self, session, round, section, audit):
        cfg = self.config
        knowledge = cfg.knowledge_retriever.retrieve(section, session['lesson_metadata'])
        memory = cfg.memory_provider.build_memory(session, round, section)
        context = cfg.context_builder.build(section, session['lesson_metadata'], memory, knowledge)
        generation = audit.started(context, knowledge)
        try:
            suggestions = cfg.suggestion_provider.generate(session, round, section, context)
            if len(suggestions) > 2:
                raise ProviderError('PROVIDER_CONTRACT', '建议提供器超过两条建议，未保存建议。')
            suggestions = [SuggestionDraft.model_validate(x) for x in suggestions]
        except ProviderError as exc:
            audit.failed(generation, exc)
            return None, exc
        except (ValueError, TypeError) as exc:
            error = ProviderError('PROVIDER_CONTRACT', '建议提供器返回值不符合接口约定，未应用修改。')
            audit.failed(generation, error)
            return None, error
        audit.completed(generation, suggestions, getattr(cfg.suggestion_provider, 'last_raw', None))
        return suggestions, None
