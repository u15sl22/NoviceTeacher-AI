from .defaults import DefaultSectionParser, DefaultRevisionStrategy, MockSuggestionProvider
from .interfaces import RevisionResult


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
        memory = fragments.get('session_memory', context.user.get('memory', {'rejected_suggestions': []}))
        legacy = context.model_copy(update={'user': {'memory': memory}})
        return MockSuggestionProvider().generate({}, {}, {'current_content': current['content']}, legacy)
