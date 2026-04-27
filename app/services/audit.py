"""Activity log writes.

`activity_log` is the single source of truth for "who did what to which
row when" — Phase 5 will rebuild it with structured actor_user_id +
source + before/after JSON. For now we keep the free-text user_name
shape that the existing UI reads.
"""
from flask import session


def log_activity(db, table, record_id, action, field="", old="", new=""):
    user = session.get("user_name", "System")
    db.execute(
        "INSERT INTO activity_log (table_name, record_id, action, field_name, old_value, new_value, user_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (table, record_id, action, field, str(old), str(new), user),
    )
