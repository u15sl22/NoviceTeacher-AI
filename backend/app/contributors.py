from typing import Protocol
from .context import ContextFragment
from .providers.interfaces import MemoryContext
from .retrieval import RetrievalQuery


class RuleBasedContextPolicy:
    rules = {'assessment': ['objectives', 'exploration', 'practice'],
        'exploration': ['objectives', 'learning_analysis'], 'practice': ['objectives', 'learning_analysis'],
        'objectives': ['learning_analysis', 'teaching_process'],
        'homework': ['objectives', 'exploration', 'practice'], 'summary': ['objectives', 'teaching_process']}

    def related_sections(self, current_section, all_sections):
        allowed = self.rules.get(current_section['section_type'], ['objectives'])
        return [s for s in all_sections if s['id'] != current_section['id'] and s['section_type'] in allowed][:3]


class LessonContextContributor:
    def __init__(self, policy): self.policy = policy

    def contribute(self, request):
        related = self.policy.related_sections(request.section, request.all_sections)
        return ContextFragment(contributor_type='lesson_context', priority=10, retention_priority=10,
            source_ids=([request.overview['id']] if request.overview else []) + [s['version_id'] for s in related],
            content={'overview': request.overview['content'] if request.overview else {},
                     'related_sections': [{'id': s['id'], 'version_id': s['version_id'], 'title': s['title'],
                        'excerpt': s['current_content'][:1600], 'excerpt_truncated': len(s['current_content']) > 1600} for s in related]},
            metadata={'lesson_overview_id': request.overview['id'] if request.overview else None,
                      'related_section_ids': [s['id'] for s in related]})


class NoMemoryProvider:
    def build_memory(self, session, current_round, current_section):
        return MemoryContext(latest_content='')


class RejectOnlyMemoryProvider:
    def __init__(self, history): self.history = history
    def build_memory(self, session, current_round, current_section):
        return MemoryContext(latest_content='', rejected_suggestions=self.history.decisions(session['id'], 'REJECT', current_section['id']))


class RuleBasedMemoryProvider:
    def __init__(self, history): self.history = history
    def build_memory(self, session, current_round, current_section):
        return MemoryContext(latest_content='',
            rejected_suggestions=self.history.decisions(session['id'], 'REJECT', current_section['id']),
            accepted_suggestions=self.history.decisions(session['id'], 'ACCEPT', None)[-12:])


class SessionMemoryContributor:
    def __init__(self, provider): self.provider = provider
    def contribute(self, request):
        memory = self.provider.build_memory(request.session, request.round, request.section)
        ids = [x['id'] for x in memory.rejected_suggestions + memory.accepted_suggestions]
        return ContextFragment(contributor_type='session_memory', content=memory.model_dump(), source_ids=ids,
                               priority=30, retention_priority=40, metadata={'memory_reference_ids': ids})


class RetrievalContributor:
    def __init__(self, retriever, kind, top_k):
        self.retriever, self.kind, self.top_k = retriever, kind, top_k
        self.last_retrieval = None
    def contribute(self, request):
        metadata = request.session['lesson_metadata']
        query = RetrievalQuery(subject=metadata['subject'], grade=metadata['grade'], topic=metadata['topic'],
            section_type=request.section['section_type'], section_content=request.section['current_content'], top_k=self.top_k)
        items = self.retriever.retrieve(query)
        self.last_retrieval = {'contributor_type': self.kind + '_retrieval', 'query_text': query.section_content,
            'filters': query.model_dump(exclude={'section_content'}), 'retriever_type': type(self.retriever).__name__,
            'retrieved_item_ids': [i.id for i in items], 'similarity_scores': [i.score for i in items],
            'top_k': query.top_k, 'items': [i.model_dump() for i in items]}
        return ContextFragment(contributor_type=self.kind + '_retrieval', content={'verified_' + self.kind: [i.model_dump() for i in items]},
            source_ids=[i.id for i in items], priority=40 if self.kind == 'knowledge' else 50,
            retention_priority=20 if self.kind == 'knowledge' else 50,
            metadata={self.kind + '_ids': [i.id for i in items], 'retrieval_record': self.last_retrieval})


class CaseRetrievalContributor(RetrievalContributor):
    def __init__(self, retriever, top_k): super().__init__(retriever, 'case', top_k)


class KnowledgeRetrievalContributor(RetrievalContributor):
    def __init__(self, retriever, top_k): super().__init__(retriever, 'knowledge', top_k)


class PreferenceProvider(Protocol):
    def get_preferences(self, user_id) -> list: ...


class NoPreferenceProvider:
    def get_preferences(self, user_id): return []


class PreferenceContributor:
    def __init__(self, provider): self.provider = provider
    def contribute(self, request):
        return ContextFragment(contributor_type='preferences', priority=60, retention_priority=60,
                               content={'preferences': self.provider.get_preferences(request.user_id)})
