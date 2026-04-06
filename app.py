#!/usr/bin/env python3
"""TaskTrack — collaborative task tracker with email-approved login."""

import csv
import io
import os
import re
import secrets
import sqlite3
from datetime import date, datetime
from functools import wraps

from flask import (
    Flask, Response, g, jsonify, redirect, render_template, request, session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")

ALLOWED_TABLES = {
    "work_tasks": {
        "fields": [
            "title",
            "cad_skill_area",
            "description",
            "requested_by",
            "request_reference",
            "priority",
            "status",
            "due_date",
            "notes",
        ],
        "required": ["title"],
        "label": "CAD Development Task",
        "status_flow": ["Not Started", "In Progress", "On Hold", "Complete"],
    },
    "project_work_tasks": {
        "fields": [
            "project_name",
            "title",
            "project_number",
            "billing_phase",
            "engineer",
            "task_description",
            "priority",
            "status",
            "due_at",
            "notes",
        ],
        "required": ["project_name", "title", "project_number", "task_description"],
        "label": "Project Work Task",
        "status_flow": ["Not Started", "In Progress", "On Hold", "Complete"],
    },
    "training_tasks": {
        "fields": [
            "title",
            "trainees",
            "requested_by",
            "skill_area",
            "training_goals",
            "additional_context",
            "priority",
            "status",
            "due_date",
            "notes",
        ],
        "required": ["title"],
        "label": "Training Task",
        "status_flow": ["Not Started", "In Progress", "On Hold", "Complete"],
    },
    "personnel_issues": {
        "fields": [
            "person_name",
            "observed_by",
            "cad_skill_area",
            "issue_description",
            "incident_context",
            "recommended_training",
            "severity",
            "status",
            "reported_date",
            "follow_up_date",
            "resolution_notes",
        ],
        "required": ["person_name", "issue_description"],
        "label": "Capability Tracking Entry",
        "status_flow": ["Observed", "Coaching Planned", "Training Scheduled", "Monitoring", "Closed"],
    },
    "suggestion_box": {
        "fields": [
            "title",
            "suggestion_type",
            "submitted_by",
            "submitted_for",
            "summary",
            "expected_value",
            "priority",
            "status",
            "review_notes",
            "promoted_work_task_id",
        ],
        "required": ["title", "summary"],
        "label": "Suggestion",
        "status_flow": ["New", "Under Review", "Approved", "Promoted to CAD", "Declined"],
    },
}

SIMPLE_SUBMISSION_CONFIGS = {
    "cad-development": {
        "table": "work_tasks",
        "source_name": "CAD Request Form",
        "page_title": "CAD Request Submission",
        "heading": "Submit a CAD Request",
        "intro": "Use this form when a CAD-related change, update, fix, or follow-up item should be logged for managers to assign.",
        "submit_label": "Submit CAD Request",
        "success_noun": "CAD request",
        "fields": [
            {"name": "title", "label": "Task Title", "type": "text", "required": True, "placeholder": "Short name for the request"},
            {"name": "requested_by", "label": "Your Name", "type": "text", "required": True, "placeholder": "Jane Smith"},
            {"name": "cad_skill_area", "label": "CAD Skill Area", "type": "text", "placeholder": "Detailing, modeling, standards, templates"},
            {"name": "description", "label": "Requested Change", "type": "textarea", "required": True, "placeholder": "What should be changed or addressed?"},
            {"name": "request_reference", "label": "Context / Follow-up Reference", "type": "textarea", "placeholder": "Who was involved and what context should stay with this request?"},
            {"name": "due_date", "label": "Needed By", "type": "date"},
        ],
    },
    "training": {
        "table": "training_tasks",
        "source_name": "Training Request Form",
        "page_title": "Training Request Submission",
        "heading": "Submit a Training Request",
        "intro": "Use this form to request coaching, training, or learning support that should be tracked as planned work.",
        "submit_label": "Submit Training Request",
        "success_noun": "training request",
        "fields": [
            {"name": "title", "label": "Training Title", "type": "text", "required": True, "placeholder": "Bluebeam markups refresher"},
            {"name": "requested_by", "label": "Your Name", "type": "text", "required": True, "placeholder": "Jane Smith"},
            {"name": "trainees", "label": "Staff Members", "type": "text", "placeholder": "Who needs the training?"},
            {"name": "skill_area", "label": "Skill Area", "type": "text", "placeholder": "Modeling, detailing, standards, automation"},
            {"name": "training_goals", "label": "Training Goals", "type": "textarea", "required": True, "placeholder": "What should be learned or improved?"},
            {"name": "additional_context", "label": "Additional Context", "type": "textarea", "placeholder": "Why is this needed right now?"},
            {"name": "due_date", "label": "Target Date", "type": "date"},
        ],
    },
    "capability": {
        "table": "personnel_issues",
        "source_name": "Capability Observation Form",
        "page_title": "Capability Observation Submission",
        "heading": "Submit a Capability Observation",
        "intro": "Use this form to document a recurring CAD skill gap, process weakness, or coaching need tied to a staff member.",
        "submit_label": "Submit Capability Note",
        "success_noun": "capability note",
        "fields": [
            {"name": "person_name", "label": "Staff Member", "type": "text", "required": True, "placeholder": "Who is this about?"},
            {"name": "observed_by", "label": "Observed By", "type": "text", "required": True, "placeholder": "Your name"},
            {"name": "cad_skill_area", "label": "CAD Skill Area", "type": "text", "placeholder": "Detailing, modeling, standards, revision control"},
            {"name": "issue_description", "label": "Observed Gap / Incident Summary", "type": "textarea", "required": True, "placeholder": "What happened or what gap keeps showing up?"},
            {"name": "incident_context", "label": "Incident Context", "type": "textarea", "placeholder": "What work or situation exposed the issue?"},
            {"name": "recommended_training", "label": "Recommended Training / Follow-Up", "type": "textarea", "placeholder": "What coaching or training would help?"},
        ],
    },
    "suggestion-box": {
        "table": "suggestion_box",
        "source_name": "Suggestion Box Form",
        "page_title": "Suggestion Box Submission",
        "heading": "Submit a Suggestion",
        "intro": "Use this form to suggest training ideas, standards, templates, automation opportunities, or other useful improvements worth reviewing.",
        "submit_label": "Submit Suggestion",
        "success_noun": "suggestion",
        "fields": [
            {"name": "title", "label": "Suggestion Title", "type": "text", "required": True, "placeholder": "Short name for the idea"},
            {"name": "submitted_by", "label": "Your Name", "type": "text", "required": True, "placeholder": "Jane Smith"},
            {"name": "submitted_for", "label": "For Review By", "type": "select", "options": ["Management", "CAD Team", "Training Leads", "Myself", "General Review"]},
            {"name": "suggestion_type", "label": "Suggestion Type", "type": "select", "options": ["Training Idea", "CAD Standard", "Template", "Automation", "Process Improvement", "Tooling", "Other"]},
            {"name": "summary", "label": "Suggestion Summary", "type": "textarea", "required": True, "placeholder": "What is the idea?"},
            {"name": "expected_value", "label": "Why This Would Help", "type": "textarea", "placeholder": "What problem would it solve or improve?"},
        ],
    },
}

ADMIN_WORKFLOW_VIEWS = {
    "project": {
        "title": "Project Work",
        "subtitle": "Manage project-linked execution work with project numbers, billing phase, engineer ownership, and due timing.",
    },
    "work": {
        "title": "CAD Development",
        "subtitle": "Track requested CAD changes, the discipline involved, and the follow-up context behind the work.",
    },
    "training": {
        "title": "Training",
        "subtitle": "Plan and track targeted training work by staff member, skill area, goals, and follow-up context.",
    },
    "personnel": {
        "title": "Capability Tracking",
        "subtitle": "Record observed CAD capability gaps over time so coaching and training needs are visible and traceable.",
    },
    "suggestions": {
        "title": "Suggestion Box",
        "subtitle": "Collect ideas for training, standards, templates, tooling, automation, and process improvements before deciding whether they should become assigned CAD work.",
    },
}


# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
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
    """)

    # Add role column if upgrading from older schema
    try:
        db.execute("SELECT role FROM users LIMIT 1")
    except sqlite3.OperationalError:
        db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        db.commit()

    ensure_column(db, "work_tasks", "cad_skill_area", "TEXT DEFAULT ''")
    ensure_column(db, "work_tasks", "requested_by", "TEXT DEFAULT ''")
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

    # Generate a persistent secret key on first run
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


def get_secret_key():
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT value FROM app_settings WHERE key = 'secret_key'").fetchone()
    db.close()
    return row[0] if row else secrets.token_hex(32)


def get_app_setting(setting_key, default_value=""):
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (setting_key,)).fetchone()
    db.close()
    return row[0] if row else default_value


def ensure_column(db, table_name, column_name, definition):
    cols = {
        row[1]
        for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in cols:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        db.commit()


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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            INSERT INTO {table} (
                id, title, cad_skill_area, description, requested_by, request_reference,
                priority, status, due_date, notes, created_by_user_id, created_by_name,
                created_at, updated_at
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
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM work_tasks
            """,
        )

    project_info = table_info_map(db, "project_work_tasks")
    project_expected = [
        "id", "project_name", "title", "project_number", "billing_phase", "engineer",
        "task_description", "priority", "status", "due_at", "notes", "created_by_user_id",
        "created_by_name", "created_at", "updated_at",
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            INSERT INTO {table} (
                id, project_name, title, project_number, billing_phase, engineer,
                task_description, priority, status, due_at, notes, created_by_user_id,
                created_by_name, created_at, updated_at
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
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM project_work_tasks
            """,
        )

    training_info = table_info_map(db, "training_tasks")
    training_expected = [
        "id", "title", "trainees", "requested_by", "skill_area", "training_goals",
        "additional_context", "priority", "status", "due_date", "notes",
        "created_by_user_id", "created_by_name", "created_at", "updated_at",
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            INSERT INTO {table} (
                id, title, trainees, requested_by, skill_area, training_goals,
                additional_context, priority, status, due_date, notes,
                created_by_user_id, created_by_name, created_at, updated_at
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
                COALESCE(updated_at, CURRENT_TIMESTAMP)
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


def overdue_field_for_table(cfg):
    if "due_at" in cfg["fields"]:
        return "due_at"
    if "follow_up_date" in cfg["fields"]:
        return "follow_up_date"
    if "due_date" in cfg["fields"]:
        return "due_date"
    return None


def done_statuses_for_table(table_name):
    if table_name == "personnel_issues":
        return {"Closed"}
    if table_name == "suggestion_box":
        return {"Promoted to CAD", "Declined"}
    return {"Complete"}


def is_overdue_value(raw_value):
    if not raw_value:
        return False

    value = str(raw_value).strip()
    if not value:
        return False

    try:
        if "T" in value:
            return datetime.fromisoformat(value) < datetime.now()
        return datetime.fromisoformat(value).date() < date.today()
    except ValueError:
        return False


def validate_record_data(table, data, creating=False):
    if table == "suggestion_box":
        for key in ("title", "suggestion_type", "submitted_by", "submitted_for", "summary", "expected_value", "review_notes"):
            if key in data and data.get(key) is not None:
                data[key] = str(data.get(key) or "").strip()
        return None

    if table != "project_work_tasks":
        return None

    project_number = (data.get("project_number") or "").strip()
    project_name = (data.get("project_name") or "").strip()
    billing_phase = (data.get("billing_phase") or "").strip()
    engineer = (data.get("engineer") or "").strip()
    task_description = (data.get("task_description") or "").strip()
    due_at = (data.get("due_at") or "").strip()

    if creating or "project_name" in data:
        if not project_name:
            return "'project_name' is required"
        data["project_name"] = project_name

    if creating or "project_number" in data:
        if not project_number:
            return "'project_number' is required"
        if not re.fullmatch(r"\d{4}\.\d{2}", project_number):
            return "Project Number must match ####.##"
        data["project_number"] = project_number

    if billing_phase:
        if not re.fullmatch(r"\d{2}", billing_phase):
            return "Project Billing Phase must match ##"
        data["billing_phase"] = billing_phase

    if creating or "engineer" in data:
        data["engineer"] = engineer

    if creating or "task_description" in data:
        if not task_description:
            return "'task_description' is required"
        data["task_description"] = task_description

    if due_at:
        try:
            datetime.fromisoformat(due_at)
        except ValueError:
            return "Due date and time must be a valid datetime"
        data["due_at"] = due_at

    return None


def extra_create_fields(table, data):
    extras = {
        "created_by_user_id": session.get("user_id"),
        "created_by_name": session.get("user_name", ""),
    }
    if "status" not in data or not str(data.get("status", "")).strip():
        extras["status"] = ALLOWED_TABLES[table]["status_flow"][0]
    return extras


def build_weekly_submission_rows(form=None, min_rows=4):
    field_names = [
        "project_number[]",
        "project_name[]",
        "title[]",
        "task_description[]",
        "billing_phase[]",
        "engineer[]",
        "due_at[]",
    ]
    if not form:
        return [{} for _ in range(min_rows)]

    values = {name: form.getlist(name) for name in field_names}
    row_count = max((len(items) for items in values.values()), default=0)
    row_count = max(row_count, min_rows)
    rows = []
    for idx in range(row_count):
        rows.append({
            "project_number": (values["project_number[]"][idx] if idx < len(values["project_number[]"]) else "").strip(),
            "project_name": (values["project_name[]"][idx] if idx < len(values["project_name[]"]) else "").strip(),
            "title": (values["title[]"][idx] if idx < len(values["title[]"]) else "").strip(),
            "task_description": (values["task_description[]"][idx] if idx < len(values["task_description[]"]) else "").strip(),
            "billing_phase": (values["billing_phase[]"][idx] if idx < len(values["billing_phase[]"]) else "").strip(),
            "engineer": (values["engineer[]"][idx] if idx < len(values["engineer[]"]) else "").strip(),
            "due_at": (values["due_at[]"][idx] if idx < len(values["due_at[]"]) else "").strip(),
        })
    return rows


def create_direct_record(db, table, payload, source_name, action="submitted", action_detail=""):
    error = validate_record_data(table, payload, creating=True)
    if error:
        return None, error

    cfg = ALLOWED_TABLES[table]
    for req in cfg["required"]:
        if not str(payload.get(req, "")).strip():
            return None, f"'{req}' is required"

    fields = [f for f in (cfg["fields"] + ["created_by_user_id", "created_by_name"]) if f in payload]
    vals = [payload[f] for f in fields]
    placeholders = ", ".join(["?"] * len(fields))
    col_names = ", ".join(fields)
    cur = db.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", vals)
    log_activity(
        db,
        table,
        cur.lastrowid,
        action,
        new=action_detail or source_name,
    )
    return cur.lastrowid, None


def log_activity(db, table, record_id, action, field="", old="", new=""):
    user = session.get("user_name", "System")
    db.execute(
        "INSERT INTO activity_log (table_name, record_id, action, field_name, old_value, new_value, user_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (table, record_id, action, field, str(old), str(new), user),
    )


# ── Auth helpers ─────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("user_role") != "admin":
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            session["user_name"] = user["display_name"]
            session["user_role"] = user["role"]
            return redirect(url_for("index"))
        error = "Invalid email or password."

    return render_template("login.html", error=error, mode="login")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None
    success = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""

        if not email or not name or not password:
            error = "All fields are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        else:
            db = get_db()
            approved = db.execute(
                "SELECT 1 FROM approved_emails WHERE email = ?", (email,)
            ).fetchone()
            if not approved:
                error = "This email is not on the approved list. Ask the admin to add you."
            else:
                existing = db.execute(
                    "SELECT 1 FROM users WHERE email = ?", (email,)
                ).fetchone()
                if existing:
                    error = "An account with this email already exists. Try logging in."
                else:
                    db.execute(
                        "INSERT INTO users (email, display_name, password_hash, role) VALUES (?, ?, ?, 'user')",
                        (email, name, generate_password_hash(password)),
                    )
                    db.commit()
                    success = "Account created! You can now log in."

    return render_template("login.html", error=error, success=success, mode="register")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── App routes ───────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        standalone_tab=None,
        standalone_title=None,
        standalone_subtitle=None,
    )


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/submit")
def submit_hub():
    forms = [
        {
            "title": "Weekly Project Work Submission",
            "copy": "Use this on Friday to submit next week’s project tasks in one batch.",
            "href": "/submit/project-work",
        },
        {
            "title": "CAD Request Submission",
            "copy": "Submit CAD changes, fixes, or manager follow-up requests without opening the dashboard.",
            "href": "/submit/cad-development",
        },
        {
            "title": "Training Request Submission",
            "copy": "Submit coaching and training needs as planned work items.",
            "href": "/submit/training",
        },
        {
            "title": "Capability Observation Submission",
            "copy": "Document staff capability gaps or incidents that should be tracked over time.",
            "href": "/submit/capability",
        },
        {
            "title": "Suggestion Box",
            "copy": "Collect ideas for training, templates, standards, automation, and process improvements.",
            "href": "/submit/suggestion-box",
        },
    ]
    return render_template("submit_hub.html", forms=forms)


@app.route("/submit/project-work", methods=["GET", "POST"])
def submit_project_work():
    rows = build_weekly_submission_rows(request.form if request.method == "POST" else None)
    submitter_name = (request.form.get("submitter_name") or "").strip() if request.method == "POST" else ""
    week_of = (request.form.get("week_of") or "").strip() if request.method == "POST" else date.today().isoformat()
    error = None
    success = None

    if request.method == "POST":
        if not submitter_name:
            error = "Your Name is required."
        elif not week_of:
            error = "Week Of is required."
        else:
            db = get_db()
            created_count = 0
            batch_id = f"weekly-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"

            for idx, row in enumerate(rows, start=1):
                if not any(row.values()):
                    continue

                payload = {
                    "project_name": row["project_name"],
                    "title": row["title"],
                    "project_number": row["project_number"],
                    "billing_phase": row["billing_phase"],
                    "engineer": row["engineer"],
                    "task_description": row["task_description"],
                    "due_at": row["due_at"],
                    "notes": (
                        f"Submitted via Weekly Work Submission\n"
                        f"Submitted by: {submitter_name}\n"
                        f"Week of: {week_of}\n"
                        f"Batch: {batch_id}"
                    ),
                }
                payload.update({
                    "created_by_user_id": None,
                    "created_by_name": "Weekly Work Submission",
                    "status": ALLOWED_TABLES["project_work_tasks"]["status_flow"][0],
                    "priority": "Medium",
                })

                _, row_error = create_direct_record(
                    db,
                    "project_work_tasks",
                    payload,
                    "Weekly Work Submission",
                    action="submitted",
                    action_detail=f"{submitter_name} | {week_of}",
                )
                if row_error:
                    error = f"Project Task {idx}: {row_error}"
                    break
                created_count += 1

            if error:
                db.rollback()
            elif created_count == 0:
                error = "Fill out at least one project task before submitting."
            else:
                db.commit()
                success = f"Submitted {created_count} project task{'s' if created_count != 1 else ''} for the week of {week_of}."
                rows = build_weekly_submission_rows(None)
                submitter_name = ""
                week_of = date.today().isoformat()

    return render_template(
        "weekly_submit.html",
        rows=rows,
        submitter_name=submitter_name,
        week_of=week_of,
        error=error,
        success=success,
    )


def render_simple_submission(config_key):
    config = SIMPLE_SUBMISSION_CONFIGS[config_key]
    values = {
        field["name"]: (request.form.get(field["name"]) or "").strip()
        for field in config["fields"]
    } if request.method == "POST" else {}
    error = None
    success = None

    if request.method == "POST":
        payload = {}
        for field in config["fields"]:
            value = (request.form.get(field["name"]) or "").strip()
            payload[field["name"]] = value

        payload.update({
            "created_by_user_id": None,
            "created_by_name": config["source_name"],
            "status": ALLOWED_TABLES[config["table"]]["status_flow"][0],
        })

        if "priority" in ALLOWED_TABLES[config["table"]]["fields"] and not payload.get("priority"):
            payload["priority"] = "Medium"
        if config["table"] == "personnel_issues" and not payload.get("severity"):
            payload["severity"] = "Medium"

        db = get_db()
        _, error = create_direct_record(
            db,
            config["table"],
            payload,
            config["source_name"],
            action="submitted",
            action_detail=payload.get("submitted_by") or payload.get("requested_by") or payload.get("observed_by") or config["source_name"],
        )
        if error:
            db.rollback()
        else:
            db.commit()
            success = f"{config['success_noun'].capitalize()} submitted successfully."
            values = {}

    return render_template(
        "simple_submit.html",
        config=config,
        values=values,
        error=error,
        success=success,
    )


@app.route("/submit/cad-development", methods=["GET", "POST"])
def submit_cad_development():
    return render_simple_submission("cad-development")


@app.route("/submit/training", methods=["GET", "POST"])
def submit_training():
    return render_simple_submission("training")


@app.route("/submit/capability", methods=["GET", "POST"])
def submit_capability():
    return render_simple_submission("capability")


@app.route("/submit/suggestion-box", methods=["GET", "POST"])
def submit_suggestion_box():
    return render_simple_submission("suggestion-box")


# ── Dashboard API ────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
@login_required
def dashboard_stats():
    db = get_db()
    stats = {}
    for table, cfg in ALLOWED_TABLES.items():
        rows = db.execute(f"SELECT * FROM {table}").fetchall()
        all_rows = [dict(r) for r in rows]
        done_statuses = done_statuses_for_table(table)
        active = [r for r in all_rows if r.get("status") not in done_statuses]
        overdue = []
        due_field = overdue_field_for_table(cfg)
        if due_field:
            overdue = [r for r in active if is_overdue_value(r.get(due_field))]

        by_status = {}
        for r in all_rows:
            s = r.get("status", "Unknown")
            by_status[s] = by_status.get(s, 0) + 1

        by_priority = {}
        p_field = "priority" if "priority" in cfg["fields"] else "severity"
        for r in all_rows:
            p = r.get(p_field, "Medium")
            by_priority[p] = by_priority.get(p, 0) + 1

        stats[table] = {
            "total": len(all_rows),
            "active": len(active),
            "overdue": len(overdue),
            "overdue_items": overdue[:10],
            "by_status": by_status,
            "by_priority": by_priority,
        }

    # Recent activity
    recent = db.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 20"
    ).fetchall()

    return jsonify({"stats": stats, "recent_activity": [dict(r) for r in recent]})


# ── Search API ───────────────────────────────────────────────────────────────

@app.route("/api/search")
@login_required
def search_records():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])

    db = get_db()
    results = []
    pattern = f"%{q}%"

    for row in db.execute(
        "SELECT id, 'work_tasks' as source, title as label, description as detail, priority, status, due_date FROM work_tasks "
        "WHERE title LIKE ? OR cad_skill_area LIKE ? OR description LIKE ? OR requested_by LIKE ? OR request_reference LIKE ? OR notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'project_work_tasks' as source, title as label, task_description as detail, priority, status, due_at as due_date FROM project_work_tasks "
        "WHERE project_name LIKE ? OR title LIKE ? OR project_number LIKE ? OR engineer LIKE ? OR task_description LIKE ? OR notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'training_tasks' as source, title as label, training_goals as detail, priority, status, due_date FROM training_tasks "
        "WHERE title LIKE ? OR trainees LIKE ? OR requested_by LIKE ? OR skill_area LIKE ? OR training_goals LIKE ? OR additional_context LIKE ? OR notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'personnel_issues' as source, person_name as label, issue_description as detail, severity as priority, status, follow_up_date as due_date FROM personnel_issues "
        "WHERE person_name LIKE ? OR observed_by LIKE ? OR cad_skill_area LIKE ? OR issue_description LIKE ? OR incident_context LIKE ? OR recommended_training LIKE ? OR resolution_notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    for row in db.execute(
        "SELECT id, 'suggestion_box' as source, title as label, summary as detail, priority, status, '' as due_date FROM suggestion_box "
        "WHERE title LIKE ? OR suggestion_type LIKE ? OR submitted_by LIKE ? OR submitted_for LIKE ? OR summary LIKE ? OR expected_value LIKE ? OR review_notes LIKE ?",
        (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
    ).fetchall():
        results.append(dict(row))

    return jsonify(results)


# ── Comments API ─────────────────────────────────────────────────────────────

@app.route("/api/<table>/<int:record_id>/comments", methods=["GET"])
@login_required
def list_comments(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    rows = db.execute(
        "SELECT * FROM comments WHERE table_name = ? AND record_id = ? ORDER BY created_at ASC",
        (table, record_id),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/<table>/<int:record_id>/comments", methods=["POST"])
@login_required
def add_comment(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Comment body is required"}), 400
    user = session.get("user_name", "Unknown")
    db = get_db()
    cur = db.execute(
        "INSERT INTO comments (table_name, record_id, user_name, body) VALUES (?, ?, ?, ?)",
        (table, record_id, user, body),
    )
    log_activity(db, table, record_id, "comment", new=body[:80])
    db.commit()
    row = db.execute("SELECT * FROM comments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


# ── Quick Status Toggle ─────────────────────────────────────────────────────

@app.route("/api/<table>/<int:record_id>/cycle-status", methods=["PUT"])
@login_required
def cycle_status(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    cfg = ALLOWED_TABLES[table]
    flow = cfg["status_flow"]
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    current = row["status"]
    try:
        idx = flow.index(current)
        new_status = flow[(idx + 1) % len(flow)]
    except ValueError:
        new_status = flow[0]
    db.execute(
        f"UPDATE {table} SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, datetime.utcnow().isoformat(), record_id),
    )
    log_activity(db, table, record_id, "status_change", "status", current, new_status)
    db.commit()
    updated = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return jsonify(dict(updated))


# ── Activity Log API ─────────────────────────────────────────────────────────

@app.route("/api/<table>/<int:record_id>/activity")
@login_required
def record_activity(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    rows = db.execute(
        "SELECT * FROM activity_log WHERE table_name = ? AND record_id = ? ORDER BY created_at DESC LIMIT 50",
        (table, record_id),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── CSV Export ───────────────────────────────────────────────────────────────

@app.route("/api/<table>/export.csv")
@login_required
def export_csv(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    rows = db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    if not rows:
        return Response("No data", mimetype="text/plain")

    output = io.StringIO()
    cols = rows[0].keys()
    writer = csv.DictWriter(output, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow(dict(r))

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={table}_{datetime.utcnow().strftime('%Y%m%d')}.csv"},
    )


# ── Admin routes ─────────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_panel():
    db = get_db()
    users = [dict(r) for r in db.execute("SELECT id, email, display_name, role, created_at FROM users ORDER BY id").fetchall()]
    emails = [dict(r) for r in db.execute("SELECT email, added_at FROM approved_emails ORDER BY email").fetchall()]
    telegram_link_code = db.execute("SELECT value FROM app_settings WHERE key = 'telegram_link_code'").fetchone()
    telegram_link_code = telegram_link_code["value"] if telegram_link_code else ""
    telegram_chats = [
        dict(r)
        for r in db.execute(
            "SELECT chat_id, username, display_name, linked_at, last_seen_at, is_active "
            "FROM telegram_chat_access ORDER BY linked_at DESC"
        ).fetchall()
    ]
    workflow_links = [
        {"key": key, "title": meta["title"], "subtitle": meta["subtitle"], "href": f"/admin/workflow/{key}"}
        for key, meta in ADMIN_WORKFLOW_VIEWS.items()
    ]
    return render_template(
        "admin.html",
        users=users,
        approved_emails=emails,
        user_name=session.get("user_name", ""),
        workflow_links=workflow_links,
        telegram_link_code=telegram_link_code,
        telegram_chats=telegram_chats,
    )


@app.route("/admin/workflow/<workflow>")
@admin_required
def admin_workflow_view(workflow):
    meta = ADMIN_WORKFLOW_VIEWS.get(workflow)
    if not meta:
        return redirect(url_for("admin_panel"))
    return render_template(
        "index.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        standalone_tab=workflow,
        standalone_title=meta["title"],
        standalone_subtitle=meta["subtitle"],
    )


@app.route("/api/admin/approved-emails", methods=["POST"])
@admin_required
def add_approved_email():
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required"}), 400
    db = get_db()
    db.execute("INSERT OR IGNORE INTO approved_emails (email) VALUES (?)", (email,))
    db.commit()
    return jsonify({"added": email}), 201


@app.route("/api/admin/approved-emails/<path:email>", methods=["DELETE"])
@admin_required
def remove_approved_email(email):
    db = get_db()
    db.execute("DELETE FROM approved_emails WHERE email = ?", (email,))
    db.commit()
    return jsonify({"removed": email})


@app.route("/api/admin/users/<int:user_id>/role", methods=["PUT"])
@admin_required
def update_user_role(user_id):
    data = request.json or {}
    role = data.get("role", "user")
    if role not in ("admin", "user"):
        return jsonify({"error": "Invalid role"}), 400
    db = get_db()
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    db.commit()
    return jsonify({"updated": user_id, "role": role})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    if user_id == session.get("user_id"):
        return jsonify({"error": "Cannot delete yourself"}), 400
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return jsonify({"deleted": user_id})


@app.route("/api/admin/users/<int:user_id>/reset-password", methods=["PUT"])
@admin_required
def reset_user_password(user_id):
    data = request.json or {}
    password = data.get("password", "")
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    db = get_db()
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
    db.commit()
    return jsonify({"reset": user_id})


@app.route("/api/admin/telegram/link-code/regenerate", methods=["PUT"])
@admin_required
def regenerate_telegram_link_code():
    code = secrets.token_hex(4).upper()
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES ('telegram_link_code', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (code,),
    )
    db.commit()
    return jsonify({"telegram_link_code": code})


@app.route("/api/admin/telegram/chats/<int:chat_id>", methods=["DELETE"])
@admin_required
def remove_telegram_chat(chat_id):
    db = get_db()
    db.execute("DELETE FROM telegram_chat_access WHERE chat_id = ?", (chat_id,))
    db.commit()
    return jsonify({"removed": chat_id})


# ── CRUD API ─────────────────────────────────────────────────────────────────

@app.route("/api/<table>", methods=["GET"])
@login_required
def list_records(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sort = request.args.get("sort", "id")
    order = request.args.get("order", "asc").upper()
    if order not in ("ASC", "DESC"):
        order = "ASC"
    all_cols = ALLOWED_TABLES[table]["fields"] + ["id", "created_at", "updated_at", "reported_date"]
    if sort not in all_cols:
        sort = "id"
    db = get_db()
    rows = db.execute(f"SELECT * FROM {table} ORDER BY {sort} {order}").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/<table>", methods=["POST"])
@login_required
def create_record(table):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    cfg = ALLOWED_TABLES[table]
    data.update(extra_create_fields(table, data))
    error = validate_record_data(table, data, creating=True)
    if error:
        return jsonify({"error": error}), 400
    for req in cfg["required"]:
        if not data.get(req, "").strip():
            return jsonify({"error": f"'{req}' is required"}), 400
    allowed_fields = cfg["fields"] + ["created_by_user_id", "created_by_name"]
    fields = [f for f in allowed_fields if f in data]
    vals = [data[f] for f in fields]
    placeholders = ", ".join(["?"] * len(fields))
    col_names = ", ".join(fields)
    db = get_db()
    cur = db.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", vals)
    log_activity(db, table, cur.lastrowid, "created", new=data.get("title") or data.get("person_name", ""))
    db.commit()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/<table>/<int:record_id>", methods=["GET"])
@login_required
def get_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(row))


@app.route("/api/<table>/<int:record_id>", methods=["PUT"])
@login_required
def update_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    data = request.json or {}
    cfg = ALLOWED_TABLES[table]
    error = validate_record_data(table, data)
    if error:
        return jsonify({"error": error}), 400
    fields = [f for f in cfg["fields"] if f in data]
    if not fields:
        return jsonify({"error": "No valid fields to update"}), 400

    db = get_db()
    old_row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not old_row:
        return jsonify({"error": "Not found"}), 404

    # Log changed fields
    for f in fields:
        old_val = old_row[f] if f in old_row.keys() else ""
        new_val = data[f]
        if str(old_val) != str(new_val):
            log_activity(db, table, record_id, "updated", f, old_val, new_val)

    sets = ", ".join([f"{f} = ?" for f in fields])
    vals = [data[f] for f in fields]
    vals.append(datetime.utcnow().isoformat())
    vals.append(record_id)
    db.execute(f"UPDATE {table} SET {sets}, updated_at = ? WHERE id = ?", vals)
    db.commit()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/<table>/<int:record_id>", methods=["DELETE"])
@login_required
def delete_record(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    db = get_db()
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    label = ""
    if row:
        label = row["title"] if "title" in row.keys() else row["person_name"] if "person_name" in row.keys() else ""
    log_activity(db, table, record_id, "deleted", new=label)
    db.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
    db.commit()
    return jsonify({"deleted": record_id})


@app.route("/api/suggestion_box/<int:record_id>/promote-to-cad", methods=["POST"])
@login_required
def promote_suggestion_to_cad(record_id):
    db = get_db()
    suggestion = db.execute("SELECT * FROM suggestion_box WHERE id = ?", (record_id,)).fetchone()
    if not suggestion:
        return jsonify({"error": "Suggestion not found"}), 404
    if suggestion["promoted_work_task_id"]:
        return jsonify({"error": "Suggestion already promoted"}), 400

    title = (suggestion["title"] or "").strip()
    summary = (suggestion["summary"] or "").strip()
    expected_value = (suggestion["expected_value"] or "").strip()
    submitted_by = (suggestion["submitted_by"] or "").strip()
    suggestion_type = (suggestion["suggestion_type"] or "").strip()

    payload = {
        "title": title,
        "cad_skill_area": suggestion_type,
        "description": summary,
        "requested_by": submitted_by,
        "request_reference": (
            f"Promoted from Suggestion Box #{record_id}\n"
            f"For review by: {suggestion['submitted_for'] or 'General Review'}\n"
            f"Why this would help: {expected_value}"
        ).strip(),
        "priority": suggestion["priority"] or "Medium",
        "status": "Not Started",
        "created_by_user_id": session.get("user_id"),
        "created_by_name": session.get("user_name", ""),
    }
    new_id, error = create_direct_record(
        db,
        "work_tasks",
        payload,
        "Suggestion Promotion",
        action="created",
        action_detail=title,
    )
    if error:
        db.rollback()
        return jsonify({"error": error}), 400

    review_notes = (suggestion["review_notes"] or "").strip()
    if review_notes:
        review_notes += "\n\n"
    review_notes += f"Promoted to CAD Development task #{new_id} on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} by {session.get('user_name', 'Unknown')}."
    db.execute(
        "UPDATE suggestion_box SET status = ?, promoted_work_task_id = ?, review_notes = ?, updated_at = ? WHERE id = ?",
        ("Promoted to CAD", new_id, review_notes, datetime.utcnow().isoformat(), record_id),
    )
    log_activity(db, "suggestion_box", record_id, "promoted", new=f"CAD task #{new_id}")
    db.commit()
    row = db.execute("SELECT * FROM suggestion_box WHERE id = ?", (record_id,)).fetchone()
    return jsonify({"suggestion": dict(row), "work_task_id": new_id})


# ── Startup ──────────────────────────────────────────────────────────────────

init_db()
app.secret_key = get_secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True

if __name__ == "__main__":
    print(f"  Database: {DB_PATH}")
    print(f"  Access:   http://0.0.0.0:5050")
    app.run(host="0.0.0.0", port=5050)
