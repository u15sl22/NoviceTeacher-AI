from .providers.interfaces import SuggestionDraft
from .providers.llm import ProviderError


class ReviewOrchestrator:
    """Identity/scope and transaction are supplied by Workflow. No capability-specific branches."""
    def __init__(self, config): self.config = config

    def generate(self, request, audit):
        generation = None
        try:
            context = self.config.context_engine.prepare(request, audit)
            generation = audit.started(context)
            suggestions = self.config.suggestion_provider.generate(context)
            if len(suggestions) > 2: raise ValueError('More than two suggestions')
            suggestions = [SuggestionDraft.model_validate(x) for x in suggestions]
            audit.completed(generation, suggestions, getattr(self.config.suggestion_provider, 'last_raw', None))
        except ProviderError as error:
            audit.failed(generation, error)
            return None, error
        except (ValueError, TypeError):
            error = ProviderError('PROVIDER_CONTRACT', '上下文或建议不符合接口约定，未应用修改。')
            audit.failed(generation, error)
            return None, error
        return suggestions, None
