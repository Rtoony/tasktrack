"""Activity log writes.

`activity_log` is the single source of truth for "who did what to which
row when" — Phase 5 will rebuild it with structured actor_user_id +
source + before/after JSON. For now we keep the free-text user_name
shape that the existing UI reads.
"""
from flask import session as flask_session
from sqlalchemy.orm import Session

from ..models import ActivityLog


def log_activity(sess: Session, table, record_id, action, field="", old="", new=""):
    """Append a row to activity_log within the caller's session.

    The caller commits (or rolls back) the session as part of the
    surrounding request's transaction.
    """
    user = flask_session.get("user_name", "System")
    sess.add(ActivityLog(
        table_name=table,
        record_id=record_id,
        action=action,
        field_name=field,
        old_value=str(old),
        new_value=str(new),
        user_name=user,
    ))
