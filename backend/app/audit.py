import json
from . import models as m
from .context import REFINEMENT_PROMPT_VERSION
from .providers.llm import ProviderError


class GenerationAudit:
    def __init__(self, workflow, session, round, section, review,
                 generation_kind='INITIAL', parent_suggestion=None, custom_prompt=None):
        self.w, self.session, self.round, self.section, self.review = workflow, session, round, section, review
        self.generation_kind = generation_kind
        self.parent_suggestion = parent_suggestion
        self.custom_prompt = custom_prompt
        self.sources = {}

    @staticmethod
    def _payload(context):
        return context.model_dump(mode='json') if hasattr(context, 'model_dump') else context

    @staticmethod
    def _fragments(payload):
        if 'contexts' in payload:
            return [fragment for context in payload['contexts']
                    for fragment in context.get('user', {}).get('fragments', [])]
        return payload.get('user', {}).get('fragments', [])

    def load_sources(self, context):
        for fragment in self._fragments(self._payload(context)):
            for item in fragment.get('content', {}).get('verified_knowledge', []):
                self.sources[item['id']] = item

    def retrieved(self, record):
        row = self.w.add(m.RetrievalRecord, session_id=self.session.id, round_id=self.round.id,
                        section_id=self.section.id, **record)
        self.w.event(self.session, 'RETRIEVAL_COMPLETED', {'retrieval_record_id': row.id}, self.round.id, self.section.id)

    def started(self, context):
        payload = self._payload(context)
        generation = self.w.add(m.GenerationRecord, session_id=self.session.id, round_id=self.round.id,
            section_id=self.section.id, section_version_id=self.w.latest(self.section.id).id,
            provider_type=self.session.config_snapshot['provider'], runtime_context=payload,
            generation_kind=self.generation_kind,
            parent_suggestion_id=self.parent_suggestion.id if self.parent_suggestion else None,
            custom_prompt_id=self.custom_prompt.id if self.custom_prompt else None)
        if self.custom_prompt:
            self.custom_prompt.generation_id = generation.id
        fragments = self._fragments(payload)
        def refs(key):
            return [v for f in fragments for v in f.get('metadata', {}).get(key, [])]
        self.load_sources(payload)
        overview = next((f['metadata'].get('lesson_overview_id') for f in fragments if f['metadata'].get('lesson_overview_id')), None)
        cfg = self.session.config_snapshot
        self.w.add(m.ContextSnapshot, session_id=self.session.id, round_id=self.round.id, section_id=self.section.id,
            generation_id=generation.id, current_section_version_id=generation.section_version_id,
            contributor_names=[f['contributor_type'] for f in fragments],
            fragment_source_ids={f['contributor_type']: f['source_ids'] for f in fragments}, lesson_overview_id=overview,
            related_section_ids=refs('related_section_ids'), memory_reference_ids=refs('memory_reference_ids'),
            preference_ids=refs('preference_ids'), knowledge_ids=refs('knowledge_ids'), case_ids=refs('case_ids'),
            prompt_version=(REFINEMENT_PROMPT_VERSION if self.generation_kind == 'REFINEMENT'
                            else cfg.get('prompt_version', 'legacy_v1')),
            model_provider=cfg.get('model_provider', cfg['provider']),
            model_name=cfg['model'], system_config_version=cfg['version'], context_json=payload)
        return generation

    def failed(self, generation, error):
        if generation is None:
            generation = self.w.add(m.GenerationRecord, session_id=self.session.id, round_id=self.round.id,
                section_id=self.section.id, section_version_id=self.w.latest(self.section.id).id,
                provider_type=self.session.config_snapshot['provider'], runtime_context={'pre_model_failure': True},
                generation_kind=self.generation_kind,
                parent_suggestion_id=self.parent_suggestion.id if self.parent_suggestion else None,
                custom_prompt_id=self.custom_prompt.id if self.custom_prompt else None)
            if self.custom_prompt:
                self.custom_prompt.generation_id = generation.id
        generation.error_code, generation.raw_response = error.code, error.raw
        self.w.event(self.session, 'SUGGESTION_GENERATION_FAILED', {'generation_id': generation.id, 'error_code': error.code},
                     self.round.id, self.section.id)

    def completed(self, generation, suggestions, raw):
        generation.raw_response = raw or {'suggestions': [x.model_dump() for x in suggestions]}
        if self.generation_kind == 'REFINEMENT' and len(suggestions) != 1:
            raise ProviderError('REFINEMENT_RESULT_COUNT', '自定义再生成必须返回恰好一条建议，可以重试。',
                                generation.raw_response)
        if any(not set(d.knowledge_ids) <= self.sources.keys() for d in suggestions):
            raise ProviderError('INVALID_CITATION', '模型引用了未进入本次上下文的知识，未保存建议。', generation.raw_response)
        ids = []
        for i, draft in enumerate(suggestions, 1):
            basis = 'verified_source' if draft.knowledge_ids else ('mock' if generation.provider_type == 'mock' else 'provisional_model')
            if self.parent_suggestion:
                thread_id = self.parent_suggestion.thread_id
                attempt_number = self.parent_suggestion.attempt_number + 1
                suggestion_index = self.parent_suggestion.suggestion_index
                parent_id = self.parent_suggestion.id
            else:
                thread_id, attempt_number, suggestion_index, parent_id = m.uid(), 1, i, None
            suggestion = self.w.add(m.Suggestion, section_id=self.section.id, round_id=self.round.id,
                generation_id=generation.id, suggestion_index=suggestion_index,
                thread_id=thread_id, parent_suggestion_id=parent_id, attempt_number=attempt_number,
                provider_type=generation.provider_type,
                basis_type=basis, basis_sources=[self.sources[k] for k in draft.knowledge_ids], **draft.model_dump())
            ids.append(suggestion.id)
            self.w.chat(self.session, 'assistant', json.dumps(draft.model_dump(), ensure_ascii=False),
                        'suggestion', self.section.id, suggestion.id)
        if self.generation_kind == 'INITIAL':
            self.review.generated_at = m.now()
            event_type = 'SUGGESTION_GENERATED'
        else:
            event_type = 'SUGGESTION_REFINED'
        self.w.event(self.session, event_type, {'generation_id': generation.id, 'suggestion_ids': ids,
                     'parent_suggestion_id': self.parent_suggestion.id if self.parent_suggestion else None,
                     'section_version_id': generation.section_version_id}, self.round.id, self.section.id)
