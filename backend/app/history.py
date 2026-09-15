from sqlalchemy import select
from .models import Suggestion, Decision


class HistoryReader:
    def __init__(self, db):
        self.db = db

    def rejected_suggestions(self, session_id, section_id):
        rows = self.db.scalars(select(Suggestion).join(Decision, Decision.suggestion_id == Suggestion.id)
            .where(Decision.session_id == session_id, Decision.section_id == section_id,
                   Decision.decision == 'REJECT').order_by(Decision.created_at, Decision.id)).all()
        return [{'id': x.id, 'issue': x.issue, 'revision': x.revision} for x in rows]
