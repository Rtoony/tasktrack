"""SQLite plumbing: connection lifecycle, schema init, additive migrations, settings.

Phase 1D will replace `init_db` / `ensure_column` / `normalize_ticket_tables`
with SQLAlchemy + Alembic. Until then this module owns the entire schema.

`DB_PATH` stays exported at module scope because telegram_bot.py imports
it directly via `from app import DB_PATH`. The module-level value is a
default; the live Flask app reads `current_app.config["DB_PATH"]`.
"""
import os
import secrets
import sqlite3

from flask import current_app, g

from .config import ALLOWED_TABLES

# Project root is one level up from this package.
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tracker.db",
)


def get_db():
    if "db" not in g:
        path = current_app.config.get("DB_PATH", DB_PATH)
        g.db = sqlite3.connect(path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db(db_path=None):
    db = sqlite3.connect(db_path or DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approved_emails (
            email TEXT PRIMARY KEY COLLATE NOCASE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS work_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            cad_skill_area TEXT DEFAULT '',
            description TEXT DEFAULT '',
            requested_by TEXT DEFAULT '',
            request_reference TEXT DEFAULT '',
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Not Started',
            due_date TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_by_user_id INTEGER DEFAULT NULL,
            created_by_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS project_work_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            project_name TEXT DEFAULT '',
            project_number TEXT DEFAULT '',
            billing_phase TEXT DEFAULT '',
            engineer TEXT DEFAULT '',
            task_description TEXT DEFAULT '',
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Not Started',
            due_at TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_by_user_id INTEGER DEFAULT NULL,
            created_by_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS training_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            trainees TEXT DEFAULT '',
            requested_by TEXT DEFAULT '',
            skill_area TEXT DEFAULT '',
            training_goals TEXT DEFAULT '',
            additional_context TEXT DEFAULT '',
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Not Started',
            due_date TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_by_user_id INTEGER DEFAULT NULL,
            created_by_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS personnel_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name TEXT NOT NULL,
            observed_by TEXT DEFAULT '',
            cad_skill_area TEXT DEFAULT '',
            issue_description TEXT NOT NULL,
            incident_context TEXT DEFAULT '',
            recommended_training TEXT DEFAULT '',
            severity TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Observed',
            reported_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            follow_up_date TEXT DEFAULT '',
            resolution_notes TEXT DEFAULT '',
            created_by_user_id INTEGER DEFAULT NULL,
            created_by_name TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS suggestion_box (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            suggestion_type TEXT DEFAULT '',
            submitted_by TEXT DEFAULT '',
            submitted_for TEXT DEFAULT 'Management',
            summary TEXT DEFAULT '',
            expected_value TEXT DEFAULT '',
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'New',
            review_notes TEXT DEFAULT '',
            promoted_work_task_id INTEGER DEFAULT NULL,
            created_by_user_id INTEGER DEFAULT NULL,
            created_by_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            field_name TEXT DEFAULT '',
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            user_name TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS telegram_chat_access (
            chat_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            display_name TEXT DEFAULT '',
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS personal_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'Personal',
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'Not Started',
            due_date TEXT DEFAULT '',
            recurrence TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            source TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_personal_tasks_status ON personal_tasks(status);
        CREATE INDEX IF NOT EXISTS idx_personal_tasks_completed ON personal_tasks(completed_at);
    """)

    try:
        db.execute("SELECT role FROM users LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        db.commit()

    ensure_column(db, "work_tasks", "cad_skill_area", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "requested_by", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "starter_note", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "clarifications_needed", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "software", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "needs_review", "INTEGER DEFAULT 0")
    ensure_column(db, "work_tasks", "source", "TEXT DEFAULT 'manual'")
    ensure_column(db, "work_tasks", "ai_raw_input", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "ai_model", "TEXT DEFAULT ''")
    for ai_tbl in ("project_work_tasks", "training_tasks"):
        ensure_column(db, ai_tbl, "needs_review", "INTEGER DEFAULT 0")
        ensure_column(db, ai_tbl, "source", "TEXT DEFAULT 'manual'")
        ensure_column(db, ai_tbl, "ai_raw_input", "TEXT DEFAULT ''")
        ensure_column(db, ai_tbl, "ai_model", "TEXT DEFAULT ''")
    ensure_column(db, "project_work_tasks", "project_name", "TEXT DEFAULT ''")
    ensure_column(db, "project_work_tasks", "project_number", "TEXT DEFAULT ''")
    ensure_column(db, "project_work_tasks", "billing_phase", "TEXT DEFAULT ''")
    ensure_column(db, "project_work_tasks", "engineer", "TEXT DEFAULT ''")
    ensure_column(db, "project_work_tasks", "task_description", "TEXT DEFAULT ''")
    ensure_column(db, "project_work_tasks", "due_at", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "request_reference", "TEXT DEFAULT ''")
    ensure_column(db, "training_tasks", "requested_by", "TEXT DEFAULT ''")
    ensure_column(db, "training_tasks", "skill_area", "TEXT DEFAULT ''")
    ensure_column(db, "training_tasks", "training_goals", "TEXT DEFAULT ''")
    ensure_column(db, "training_tasks", "additional_context", "TEXT DEFAULT ''")
    ensure_column(db, "personnel_issues", "observed_by", "TEXT DEFAULT ''")
    ensure_column(db, "personnel_issues", "cad_skill_area", "TEXT DEFAULT ''")
    ensure_column(db, "personnel_issues", "incident_context", "TEXT DEFAULT ''")
    ensure_column(db, "personnel_issues", "recommended_training", "TEXT DEFAULT ''")
    ensure_column(db, "personnel_issues", "follow_up_date", "TEXT DEFAULT ''")
    ensure_column(db, "suggestion_box", "suggestion_type", "TEXT DEFAULT ''")
    ensure_column(db, "suggestion_box", "submitted_by", "TEXT DEFAULT ''")
    ensure_column(db, "suggestion_box", "submitted_for", "TEXT DEFAULT 'Management'")
    ensure_column(db, "suggestion_box", "summary", "TEXT DEFAULT ''")
    ensure_column(db, "suggestion_box", "expected_value", "TEXT DEFAULT ''")
    ensure_column(db, "suggestion_box", "review_notes", "TEXT DEFAULT ''")
    ensure_column(db, "suggestion_box", "promoted_work_task_id", "INTEGER DEFAULT NULL")
    for table_name in ALLOWED_TABLES:
        ensure_column(db, table_name, "created_by_user_id", "INTEGER DEFAULT NULL")
        ensure_column(db, table_name, "created_by_name", "TEXT DEFAULT ''")
    normalize_ticket_tables(db)

    row = db.execute("SELECT value FROM app_settings WHERE key = 'secret_key'").fetchone()
    if not row:
        key = secrets.token_hex(32)
        db.execute("INSERT INTO app_settings (key, value) VALUES ('secret_key', ?)", (key,))
        db.commit()

    row = db.execute("SELECT value FROM app_settings WHERE key = 'telegram_link_code'").fetchone()
    if not row:
        code = secrets.token_hex(4).upper()
        db.execute("INSERT INTO app_settings (key, value) VALUES ('telegram_link_code', ?)", (code,))
        db.commit()

    db.close()


def get_secret_key(db_path=None):
    db = sqlite3.connect(db_path or DB_PATH)
    row = db.execute("SELECT value FROM app_settings WHERE key = 'secret_key'").fetchone()
    db.close()
    return row[0] if row else secrets.token_hex(32)


def get_app_setting(setting_key, default_value="", db_path=None):
    db = sqlite3.connect(db_path or DB_PATH)
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (setting_key,)).fetchone()
    db.close()
    return row[0] if row else default_value


def ensure_column(db, table_name, column_name, definition):
    cols = {
        row[1]
        for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in cols:
        return
    try:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        db.commit()
    except sqlite3.OperationalError as err:
        # Concurrent boot workers can race the ALTER TABLE. If another worker
        # already added the column, swallow the "duplicate column name" error.
        if "duplicate column" not in str(err).lower():
            raise


def table_info_map(db, table_name):
    return {
        row[1]: {
            "type": row[2],
            "notnull": row[3],
            "default": row[4],
            "pk": row[5],
        }
        for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def rebuild_table(db, table_name, create_sql, insert_sql):
    temp_name = f"{table_name}__new"
    db.execute(f"DROP TABLE IF EXISTS {temp_name}")
    db.execute(create_sql.format(table=temp_name))
    db.execute(insert_sql.format(table=temp_name))
    db.execute(f"DROP TABLE {table_name}")
    db.execute(f"ALTER TABLE {temp_name} RENAME TO {table_name}")
    db.commit()


def normalize_ticket_tables(db):
    work_info = table_info_map(db, "work_tasks")
    work_expected = [
        "id", "title", "cad_skill_area", "description", "requested_by", "request_reference",
        "priority", "status", "due_date", "notes", "created_by_user_id", "created_by_name",
        "created_at", "updated_at",
        "starter_note", "clarifications_needed", "software", "needs_review", "source",
        "ai_raw_input", "ai_model",
    ]
    if list(work_info.keys()) != work_expected:
        rebuild_table(
            db,
            "work_tasks",
            """
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                cad_skill_area TEXT DEFAULT '',
                description TEXT DEFAULT '',
                requested_by TEXT DEFAULT '',
                request_reference TEXT DEFAULT '',
                priority TEXT DEFAULT 'Medium',
                status TEXT DEFAULT 'Not Started',
                due_date TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_by_user_id INTEGER DEFAULT NULL,
                created_by_name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                starter_note TEXT DEFAULT '',
                clarifications_needed TEXT DEFAULT '',
                software TEXT DEFAULT '',
                needs_review INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                ai_raw_input TEXT DEFAULT '',
                ai_model TEXT DEFAULT ''
            )
            """,
            """
            INSERT INTO {table} (
                id, title, cad_skill_area, description, requested_by, request_reference,
                priority, status, due_date, notes, created_by_user_id, created_by_name,
                created_at, updated_at,
                starter_note, clarifications_needed, software, needs_review, source,
                ai_raw_input, ai_model
            )
            SELECT
                id,
                COALESCE(title, ''),
                COALESCE(cad_skill_area, ''),
                COALESCE(description, ''),
                COALESCE(requested_by, ''),
                COALESCE(request_reference, ''),
                COALESCE(priority, 'Medium'),
                COALESCE(status, 'Not Started'),
                COALESCE(due_date, ''),
                COALESCE(notes, ''),
                created_by_user_id,
                COALESCE(created_by_name, ''),
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP),
                COALESCE(starter_note, ''),
                COALESCE(clarifications_needed, ''),
                COALESCE(software, ''),
                COALESCE(needs_review, 0),
                COALESCE(NULLIF(source, ''), 'manual'),
                COALESCE(ai_raw_input, ''),
                COALESCE(ai_model, '')
            FROM work_tasks
            """,
        )

    project_info = table_info_map(db, "project_work_tasks")
    project_expected = [
        "id", "project_name", "title", "project_number", "billing_phase", "engineer",
        "task_description", "priority", "status", "due_at", "notes", "created_by_user_id",
        "created_by_name", "created_at", "updated_at",
        "needs_review", "source", "ai_raw_input", "ai_model",
    ]
    if list(project_info.keys()) != project_expected:
        rebuild_table(
            db,
            "project_work_tasks",
            """
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT DEFAULT '',
                title TEXT NOT NULL,
                project_number TEXT DEFAULT '',
                billing_phase TEXT DEFAULT '',
                engineer TEXT DEFAULT '',
                task_description TEXT DEFAULT '',
                priority TEXT DEFAULT 'Medium',
                status TEXT DEFAULT 'Not Started',
                due_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_by_user_id INTEGER DEFAULT NULL,
                created_by_name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                needs_review INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                ai_raw_input TEXT DEFAULT '',
                ai_model TEXT DEFAULT ''
            )
            """,
            """
            INSERT INTO {table} (
                id, project_name, title, project_number, billing_phase, engineer,
                task_description, priority, status, due_at, notes, created_by_user_id,
                created_by_name, created_at, updated_at,
                needs_review, source, ai_raw_input, ai_model
            )
            SELECT
                id,
                COALESCE(NULLIF(project_name, ''), title, ''),
                COALESCE(title, ''),
                COALESCE(project_number, ''),
                COALESCE(billing_phase, ''),
                COALESCE(engineer, ''),
                COALESCE(NULLIF(task_description, ''), description, ''),
                COALESCE(priority, 'Medium'),
                COALESCE(status, 'Not Started'),
                COALESCE(NULLIF(due_at, ''), due_date, ''),
                COALESCE(notes, ''),
                created_by_user_id,
                COALESCE(created_by_name, ''),
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP),
                COALESCE(needs_review, 0),
                COALESCE(NULLIF(source, ''), 'manual'),
                COALESCE(ai_raw_input, ''),
                COALESCE(ai_model, '')
            FROM project_work_tasks
            """,
        )

    training_info = table_info_map(db, "training_tasks")
    training_expected = [
        "id", "title", "trainees", "requested_by", "skill_area", "training_goals",
        "additional_context", "priority", "status", "due_date", "notes",
        "created_by_user_id", "created_by_name", "created_at", "updated_at",
        "needs_review", "source", "ai_raw_input", "ai_model",
    ]
    if list(training_info.keys()) != training_expected:
        rebuild_table(
            db,
            "training_tasks",
            """
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                trainees TEXT DEFAULT '',
                requested_by TEXT DEFAULT '',
                skill_area TEXT DEFAULT '',
                training_goals TEXT DEFAULT '',
                additional_context TEXT DEFAULT '',
                priority TEXT DEFAULT 'Medium',
                status TEXT DEFAULT 'Not Started',
                due_date TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_by_user_id INTEGER DEFAULT NULL,
                created_by_name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                needs_review INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                ai_raw_input TEXT DEFAULT '',
                ai_model TEXT DEFAULT ''
            )
            """,
            """
            INSERT INTO {table} (
                id, title, trainees, requested_by, skill_area, training_goals,
                additional_context, priority, status, due_date, notes,
                created_by_user_id, created_by_name, created_at, updated_at,
                needs_review, source, ai_raw_input, ai_model
            )
            SELECT
                id,
                COALESCE(title, ''),
                COALESCE(trainees, ''),
                COALESCE(requested_by, ''),
                COALESCE(skill_area, ''),
                COALESCE(NULLIF(training_goals, ''), description, ''),
                COALESCE(additional_context, ''),
                COALESCE(priority, 'Medium'),
                COALESCE(status, 'Not Started'),
                COALESCE(due_date, ''),
                COALESCE(notes, ''),
                created_by_user_id,
                COALESCE(created_by_name, ''),
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP),
                COALESCE(needs_review, 0),
                COALESCE(NULLIF(source, ''), 'manual'),
                COALESCE(ai_raw_input, ''),
                COALESCE(ai_model, '')
            FROM training_tasks
            """,
        )

    personnel_info = table_info_map(db, "personnel_issues")
    personnel_expected = [
        "id", "person_name", "observed_by", "cad_skill_area", "issue_description", "incident_context",
        "recommended_training", "severity", "status", "reported_date", "follow_up_date",
        "resolution_notes", "created_by_user_id", "created_by_name", "updated_at",
    ]
    if (
        list(personnel_info.keys()) != personnel_expected
        or personnel_info.get("status", {}).get("default") != "'Observed'"
    ):
        rebuild_table(
            db,
            "personnel_issues",
            """
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_name TEXT NOT NULL,
                observed_by TEXT DEFAULT '',
                cad_skill_area TEXT DEFAULT '',
                issue_description TEXT NOT NULL,
                incident_context TEXT DEFAULT '',
                recommended_training TEXT DEFAULT '',
                severity TEXT DEFAULT 'Medium',
                status TEXT DEFAULT 'Observed',
                reported_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                follow_up_date TEXT DEFAULT '',
                resolution_notes TEXT DEFAULT '',
                created_by_user_id INTEGER DEFAULT NULL,
                created_by_name TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            INSERT INTO {table} (
                id, person_name, observed_by, cad_skill_area, issue_description, incident_context,
                recommended_training, severity, status, reported_date, follow_up_date,
                resolution_notes, created_by_user_id, created_by_name, updated_at
            )
            SELECT
                id,
                COALESCE(person_name, ''),
                COALESCE(observed_by, ''),
                COALESCE(cad_skill_area, ''),
                COALESCE(issue_description, ''),
                COALESCE(incident_context, ''),
                COALESCE(recommended_training, ''),
                COALESCE(severity, 'Medium'),
                CASE
                    WHEN status = 'Open' THEN 'Observed'
                    WHEN status = 'Under Review' THEN 'Coaching Planned'
                    WHEN status = 'Escalated' THEN 'Training Scheduled'
                    WHEN status = 'Resolved' THEN 'Closed'
                    WHEN status IS NULL OR status = '' THEN 'Observed'
                    ELSE status
                END,
                COALESCE(reported_date, CURRENT_TIMESTAMP),
                COALESCE(follow_up_date, ''),
                COALESCE(resolution_notes, ''),
                created_by_user_id,
                COALESCE(created_by_name, ''),
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM personnel_issues
            """,
        )

    suggestion_info = table_info_map(db, "suggestion_box")
    suggestion_expected = [
        "id", "title", "suggestion_type", "submitted_by", "submitted_for", "summary",
        "expected_value", "priority", "status", "review_notes", "promoted_work_task_id",
        "created_by_user_id", "created_by_name", "created_at", "updated_at",
    ]
    if list(suggestion_info.keys()) != suggestion_expected:
        rebuild_table(
            db,
            "suggestion_box",
            """
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                suggestion_type TEXT DEFAULT '',
                submitted_by TEXT DEFAULT '',
                submitted_for TEXT DEFAULT 'Management',
                summary TEXT DEFAULT '',
                expected_value TEXT DEFAULT '',
                priority TEXT DEFAULT 'Medium',
                status TEXT DEFAULT 'New',
                review_notes TEXT DEFAULT '',
                promoted_work_task_id INTEGER DEFAULT NULL,
                created_by_user_id INTEGER DEFAULT NULL,
                created_by_name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            INSERT INTO {table} (
                id, title, suggestion_type, submitted_by, submitted_for, summary,
                expected_value, priority, status, review_notes, promoted_work_task_id,
                created_by_user_id, created_by_name, created_at, updated_at
            )
            SELECT
                id,
                COALESCE(title, ''),
                COALESCE(suggestion_type, ''),
                COALESCE(submitted_by, ''),
                COALESCE(NULLIF(submitted_for, ''), 'Management'),
                COALESCE(summary, ''),
                COALESCE(expected_value, ''),
                COALESCE(priority, 'Medium'),
                COALESCE(NULLIF(status, ''), 'New'),
                COALESCE(review_notes, ''),
                promoted_work_task_id,
                created_by_user_id,
                COALESCE(created_by_name, ''),
                COALESCE(created_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM suggestion_box
            """,
        )
