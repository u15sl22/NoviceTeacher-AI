from sqlalchemy import select
from .models import Suggestion, Decision


class HistoryReader:
    def __init__(self, db, user_id):
        self.db, self.user_id = db, user_id

    def rejected_suggestions(self, session_id, section_id):
        rows = self.db.scalars(select(Suggestion).join(Decision, Decision.suggestion_id == Suggestion.id)
            .where(Decision.user_id == self.user_id, Decision.session_id == session_id, Decision.section_id == section_id,
                   Decision.decision == 'REJECT').order_by(Decision.created_at, Decision.id)).all()
        return [{'id': x.id, 'issue': x.issue, 'revision': x.revision} for x in rows]

    def decisions(self, session_id, decision, section_id=None):
        query = select(Suggestion, Decision).join(Decision, Decision.suggestion_id == Suggestion.id).where(
            Decision.user_id == self.user_id, Decision.session_id == session_id, Decision.decision == decision)
        if section_id: query = query.where(Decision.section_id == section_id)
        rows = self.db.execute(query.order_by(Decision.created_at, Decision.id)).all()
        return [{'id': d.id, 'suggestion_id': s.id, 'section_id': s.section_id, 'round_id': d.round_id,
                 'issue': s.issue, 'revision': s.revision} for s, d in rows]
