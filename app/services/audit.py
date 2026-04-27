"""Activity log writes.

`activity_log` is the single source of truth for "who did what to which
row when" — Phase 5 will rebuild it with structured actor_user_id +
source + before/after JSON. For now we keep the free-text user_name
shape that the existing UI reads.

Phase 1D-2 transitional dispatch: `log_activity` accepts either a raw
sqlite3.Connection (legacy callers) or a SQLAlchemy Session (converted
callers). When the last raw call site is gone (1D-2i) the dispatch
collapses back to a session-only signature.
"""
import sqlite3

from flask import session as flask_session
from sqlalchemy.orm import Session

from ..models import ActivityLog


def log_activity(db_or_session, table, record_id, action, field="", old="", new=""):
    user = flask_session.get("user_name", "System")
    if isinstance(db_or_session, sqlite3.Connection):
        db_or_session.execute(
            "INSERT INTO activity_log (table_name, record_id, action, field_name, old_value, new_value, user_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (table, record_id, action, field, str(old), str(new), user),
        )
        return
    if isinstance(db_or_session, Session):
        db_or_session.add(ActivityLog(
            table_name=table,
            record_id=record_id,
            action=action,
            field_name=field,
            old_value=str(old),
            new_value=str(new),
            user_name=user,
        ))
        return
    raise TypeError(
        f"log_activity expects sqlite3.Connection or sqlalchemy Session, "
        f"got {type(db_or_session).__name__}"
    )
