from .defaults import DefaultSectionParser, DefaultRevisionStrategy, MockSuggestionProvider
from .interfaces import RevisionResult, SuggestionDraft


class BoundedSectionParser:
    def parse(self, text, lesson_metadata=None):
        drafts = DefaultSectionParser().parse(text, lesson_metadata)
        while len(drafts) > 10:
            i = min(range(len(drafts) - 1), key=lambda i: (
                drafts[i].section_type != drafts[i + 1].section_type,
                len(drafts[i].content) + len(drafts[i + 1].content)))
            a, b = drafts[i:i + 2]
            drafts[i:i + 2] = [type(a)(title=a.title + ' / ' + b.title, content=a.content + b.content,
                section_type=a.section_type if a.section_type == b.section_type else 'mixed')]
        return drafts


class LegacyRevisionAdapter:
    def apply(self, text, suggestion):
        return RevisionResult(status='applied', content=DefaultRevisionStrategy().apply(text, suggestion))


class ContextOnlyMockProvider:
    def generate(self, context):
        fragments = {f['contributor_type']: f['content'] for f in context.user.get('fragments', [])}
        current = fragments.get('current_section', context.user.get('section', {}))
        if refinement := fragments.get('refinement_request'):
            previous = refinement['previous_suggestion']
            return [SuggestionDraft(
                issue=previous['issue'],
                reason='根据教师的自定义要求重新组织修改方案（模拟建议）。',
                pedagogical_basis=previous['pedagogical_basis'],
                revision=f"{refinement['prompt_text']}\n\n模拟修订：{previous['revision']}",
                issue_type=previous['issue_type'], scope=previous['scope'],
                revision_mode=previous['revision_mode'], target_text=previous.get('target_text'),
                knowledge_ids=previous.get('knowledge_ids', []))]
        memory = fragments.get('session_memory', context.user.get('memory', {'rejected_suggestions': []}))
        legacy = context.model_copy(update={'user': {'memory': memory}})
        return MockSuggestionProvider().generate({}, {}, {'current_content': current['content']}, legacy)
