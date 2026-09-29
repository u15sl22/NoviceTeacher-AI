from .interfaces import RevisionResult
from .defaults import DefaultRevisionStrategy


class AppendRevisionStrategy:
    def apply(self, current_text, suggestion):
        return RevisionResult(status='applied', content=DefaultRevisionStrategy().apply(current_text, suggestion))


class ReplaceOrAppendRevisionStrategy:
    def apply(self, current_text, suggestion):
        if suggestion.revision_mode == 'append':
            return AppendRevisionStrategy().apply(current_text, suggestion)
        target = suggestion.target_text
        if not target or current_text.count(target) != 1:
            return RevisionResult(status='candidate', content=current_text, candidate=suggestion.revision,
                                  reason='原文定位不唯一或已变化，请检查候选修改；未覆盖正文。')
        return RevisionResult(status='applied', content=current_text.replace(target, suggestion.revision, 1))
