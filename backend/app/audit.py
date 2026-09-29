import json
from . import models as m
from .providers.llm import ProviderError


class GenerationAudit:
    def __init__(self, workflow, session, round, section, review):
        self.w, self.session, self.round, self.section, self.review = workflow, session, round, section, review
        self.sources = {}

    def retrieved(self, record):
        row = self.w.add(m.RetrievalRecord, session_id=self.session.id, round_id=self.round.id,
                        section_id=self.section.id, **record)
        self.w.event(self.session, 'RETRIEVAL_COMPLETED', {'retrieval_record_id': row.id}, self.round.id, self.section.id)

    def started(self, context):
        generation = self.w.add(m.GenerationRecord, session_id=self.session.id, round_id=self.round.id,
            section_id=self.section.id, section_version_id=self.w.latest(self.section.id).id,
            provider_type=self.session.config_snapshot['provider'], runtime_context=context.model_dump(mode='json'))
        fragments = context.user.get('fragments', [])
        def refs(key):
            return [v for f in fragments for v in f.get('metadata', {}).get(key, [])]
        for f in fragments:
            for item in f['content'].get('verified_knowledge', []): self.sources[item['id']] = item
        overview = next((f['metadata'].get('lesson_overview_id') for f in fragments if f['metadata'].get('lesson_overview_id')), None)
        cfg = self.session.config_snapshot
        self.w.add(m.ContextSnapshot, session_id=self.session.id, round_id=self.round.id, section_id=self.section.id,
            generation_id=generation.id, current_section_version_id=generation.section_version_id,
            contributor_names=[f['contributor_type'] for f in fragments],
            fragment_source_ids={f['contributor_type']: f['source_ids'] for f in fragments}, lesson_overview_id=overview,
            related_section_ids=refs('related_section_ids'), memory_reference_ids=refs('memory_reference_ids'),
            preference_ids=refs('preference_ids'), knowledge_ids=refs('knowledge_ids'), case_ids=refs('case_ids'),
            prompt_version=cfg.get('prompt_version', 'legacy_v1'), model_provider=cfg.get('model_provider', cfg['provider']),
            model_name=cfg['model'], system_config_version=cfg['version'], context_json=context.model_dump(mode='json'))
        return generation

    def failed(self, generation, error):
        if generation is None:
            generation = self.w.add(m.GenerationRecord, session_id=self.session.id, round_id=self.round.id,
                section_id=self.section.id, section_version_id=self.w.latest(self.section.id).id,
                provider_type=self.session.config_snapshot['provider'], runtime_context={'pre_model_failure': True})
        generation.error_code, generation.raw_response = error.code, error.raw
        self.w.event(self.session, 'SUGGESTION_GENERATION_FAILED', {'generation_id': generation.id, 'error_code': error.code},
                     self.round.id, self.section.id)

    def completed(self, generation, suggestions, raw):
        generation.raw_response = raw or {'suggestions': [x.model_dump() for x in suggestions]}
        if any(not set(d.knowledge_ids) <= self.sources.keys() for d in suggestions):
            raise ProviderError('INVALID_CITATION', '模型引用了未进入本次上下文的知识，未保存建议。', generation.raw_response)
        ids = []
        for i, draft in enumerate(suggestions, 1):
            basis = 'verified_source' if draft.knowledge_ids else ('mock' if generation.provider_type == 'mock' else 'provisional_model')
            suggestion = self.w.add(m.Suggestion, section_id=self.section.id, round_id=self.round.id,
                generation_id=generation.id, suggestion_index=i, provider_type=generation.provider_type,
                basis_type=basis, basis_sources=[self.sources[k] for k in draft.knowledge_ids], **draft.model_dump())
            ids.append(suggestion.id)
            self.w.chat(self.session, 'assistant', json.dumps(draft.model_dump(), ensure_ascii=False),
                        'suggestion', self.section.id, suggestion.id)
        self.review.generated_at = m.now()
        self.w.event(self.session, 'SUGGESTION_GENERATED', {'generation_id': generation.id, 'suggestion_ids': ids,
                     'section_version_id': generation.section_version_id}, self.round.id, self.section.id)
