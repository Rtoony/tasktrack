"""Tests for app/routes/admin.py — admin panel + user/email/telegram mgmt.

Covers:
- @admin_required gates every state-changing endpoint
- Approved-emails CRUD
- User role toggles, deletion, password reset
- Telegram link-code regeneration + chat removal
- /admin and /admin/workflow/<workflow> render for admins, redirect others

All endpoints are exercised through the Flask test client (TESTING=True so
CSRF and rate limits are bypassed).
"""
import json

from app.db import get_session
from app.models import ApprovedEmail, ManagedOption, ManagedOptionSet, TelegramChatAccess, User

# ── Auth gating ───────────────────────────────────────────────────────────

def test_admin_panel_redirects_anonymous(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_admin_panel_forbids_regular_user(auth_client):
    # Regular user (role=user) gets redirected to / per admin_required
    r = auth_client.get("/admin", follow_redirects=False)
    assert r.status_code in (302, 403)


def test_admin_panel_serves_admin(admin_client):
    r = admin_client.get("/admin")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Admin Control Center" in html
    assert "Admin Workbench" in html
    assert "/admin/dropdowns" in html
    assert "/admin/people" in html
    assert "/admin/projects" in html
    assert "/admin/access" in html
    assert "/admin/reports" in html
    assert "Configuration Coverage" in html

    reports = admin_client.get("/admin/reports")
    assert reports.status_code == 200
    report_html = reports.get_data(as_text=True)
    assert "Report Shortcut Editor" in report_html
    assert "Report Console Quick Actions" in report_html
    assert "Pages & Reports" in report_html
    assert "Report Center" in report_html
    assert "Today Brief" in report_html
    assert "Management Packet" in report_html
    assert "/reports/management" in report_html
    assert "Portfolio Reports" in report_html
    assert "At-Risk Queue" in report_html
    assert "At-Risk CSV" in report_html
    assert "Project One-Pager" in report_html
    assert "Meeting Packet Batch" in report_html
    assert "Weekly Review" in report_html
    assert "Submission Forms" in report_html
    assert "Printable Intake Packet" in report_html
    assert "/intake/printable" in report_html
    assert "/reports/today" in report_html
    assert "/reports/meetings?days=14&amp;limit=12" in report_html


def test_admin_section_routes_use_control_center_shell(admin_client):
    for path, expected in [
        ("/admin/dropdowns", "Managed Dropdowns"),
        ("/admin/people", "Employee Roster"),
        ("/admin/projects", "Project List"),
        ("/admin/access", "Telegram Bot Access"),
        ("/admin/intake", "Intake Controls"),
        ("/admin/reports", "Pages & Reports"),
        ("/admin/system", "Configuration Coverage"),
    ]:
        r = admin_client.get(path)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Admin Control Center" in html
        assert expected in html
        assert "Admin ·" not in html


def test_admin_shortcuts_and_coverage_are_managed_options(admin_client, temp_app):
    rows = admin_client.get("/api/v1/admin/options/sets?include_inactive=1").get_json()
    by_key = {row["key"]: row for row in rows}
    assert "admin_report_shortcut" in by_key
    assert "report_console_quick_action" in by_key
    assert "admin_control_inventory" in by_key
    report_center = next(
        opt for opt in by_key["admin_report_shortcut"]["options"]
        if opt["value"] == "report_center"
    )
    assert report_center["metadata"]["href"] == "/reports"

    created = admin_client.post(
        "/api/v1/admin/options/sets/admin_report_shortcut/options",
        json={
            "value": "custom_packet",
            "label": "Custom Packet",
            "description": "Office-specific recurring packet.",
            "display_order": 210,
            "metadata": {"href": "/reports/custom", "admin_only": False},
        },
    )
    assert created.status_code == 201
    html = admin_client.get("/admin/reports").get_data(as_text=True)
    assert "Custom Packet" in html
    assert "/reports/custom" in html


def test_admin_workflow_project_uses_standalone_shell(admin_client):
    r = admin_client.get("/admin/workflow/project")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="app-shell app-shell-standalone"' in html
    assert 'class="side-nav"' not in html
    assert 'id="global-search"' not in html
    assert 'id="health-pill"' not in html
    assert 'Full Tracker' in html
    assert 'const STANDALONE_TAB = "project";' in html
    assert 'id="sec-project"' in html
    assert 'if (!section || (!btn && STANDALONE_TAB !== tabName)) return false;' in html


def test_admin_workflow_redirects_regular_user(auth_client):
    r = auth_client.get("/admin/workflow/project", follow_redirects=False)
    assert r.status_code in (302, 403)



def test_admin_api_endpoint_returns_401_for_anonymous(client):
    r = client.post("/api/v1/admin/approved-emails", json={"email": "x@y.com"})
    assert r.status_code == 401


def test_admin_api_endpoint_returns_403_for_regular_user(auth_client):
    r = auth_client.post("/api/v1/admin/approved-emails", json={"email": "x@y.com"})
    assert r.status_code == 403


# ── Managed option registry ──────────────────────────────────────────────

def test_option_values_require_login(client):
    r = client.get("/api/v1/options/cad_skill_area")
    assert r.status_code == 401


def test_option_values_seed_defaults(auth_client):
    r = auth_client.get("/api/v1/options/cad_skill_area")
    assert r.status_code == 200
    rows = r.get_json()
    assert any(row["label"] == "AutoCAD Core" for row in rows)
    assert any(row.get("skill_category_id") for row in rows)


def test_managed_option_admin_routes_are_admin_only(auth_client):
    r = auth_client.get("/api/v1/admin/options/sets")
    assert r.status_code == 403


def test_managed_option_defaults_include_metadata(auth_client):
    priorities = auth_client.get("/api/v1/options/task_priority")
    assert priorities.status_code == 200
    priority_rows = priorities.get_json()
    assert [row["value"] for row in priority_rows] == ["Low", "Medium", "High"]
    medium = next(row for row in priority_rows if row["value"] == "Medium")
    high = next(row for row in priority_rows if row["value"] == "High")
    assert medium["is_default"] is True
    assert medium["tone"] == "warning"
    assert medium["metadata"]["rank"] == 20
    assert high["metadata"]["rank"] == 10

    severities = auth_client.get("/api/v1/options/incident_severity")
    assert severities.status_code == 200
    severity_rows = severities.get_json()
    assert [row["value"] for row in severity_rows] == ["Low", "Medium", "High", "Critical"]
    critical = next(row for row in severity_rows if row["value"] == "Critical")
    assert critical["tone"] == "danger"
    assert critical["metadata"]["is_high_severity"] is True

    statuses = auth_client.get("/api/v1/options/project_display_status")
    assert statuses.status_code == 200
    status_rows = statuses.get_json()
    active = next(row for row in status_rows if row["value"] == "active")
    dormant = next(row for row in status_rows if row["value"] == "dormant")
    assert active["is_default"] is True
    assert dormant["is_terminal"] is True
    assert dormant["counts_as_open"] is False


def test_managed_option_seed_backfills_missing_metadata(auth_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        option_set = ManagedOptionSet(
            key="task_priority",
            label="Task Priorities",
            surface="Tasks",
            is_system=1,
            active=1,
        )
        sess.add(option_set)
        sess.flush()
        for order, value in enumerate(["Low", "Medium", "High"], start=1):
            sess.add(ManagedOption(
                set_id=option_set.id,
                value=value,
                label=value,
                display_order=order * 10,
                metadata_json="{}",
            ))
        sess.commit()

    rows = auth_client.get("/api/v1/options/task_priority").get_json()
    medium = next(row for row in rows if row["value"] == "Medium")
    high = next(row for row in rows if row["value"] == "High")
    assert medium["is_default"] is True
    assert medium["metadata"]["rank"] == 20
    assert high["metadata"]["rank"] == 10


def test_admin_managed_option_metadata_roundtrip(admin_client, temp_app):
    created = admin_client.post("/api/v1/admin/options/sets/task_priority/options", json={
        "value": "Escalated",
        "label": "Escalated",
        "display_order": 5,
        "metadata": {
            "is_default": True,
            "rank": 0,
            "tone": "danger",
            "counts_as_open": True,
        },
    })
    assert created.status_code == 201
    body = created.get_json()
    option_id = body["id"]
    assert body["is_default"] is True
    assert body["tone"] == "danger"
    assert body["metadata"]["rank"] == 0

    public_rows = admin_client.get("/api/v1/options/task_priority").get_json()
    escalated = next(row for row in public_rows if row["value"] == "Escalated")
    medium = next(row for row in public_rows if row["value"] == "Medium")
    assert escalated["is_default"] is True
    assert medium["is_default"] is False

    patched = admin_client.patch(f"/api/v1/admin/options/options/{option_id}", json={
        "metadata": {
            "is_default": False,
            "is_terminal": True,
            "counts_as_open": False,
            "rank": 1,
            "tone": "warning",
        },
    })
    assert patched.status_code == 200
    patched_body = patched.get_json()
    assert patched_body["is_default"] is False
    assert patched_body["is_terminal"] is True
    assert patched_body["counts_as_open"] is False
    assert patched_body["tone"] == "warning"
    assert patched_body["metadata"]["rank"] == 1

    with temp_app.app_context():
        sess = get_session()
        option_row = sess.get(ManagedOption, option_id)
        metadata = json.loads(option_row.metadata_json)
        assert metadata["is_terminal"] is True
        assert metadata["counts_as_open"] is False
        assert metadata["rank"] == 1


def test_admin_managed_option_crud(admin_client, temp_app):
    created = admin_client.post("/api/v1/admin/options/sets", json={
        "key": "office_terms",
        "label": "Office Terms",
        "surface": "Test",
    })
    assert created.status_code == 201
    assert created.get_json()["key"] == "office_terms"

    option = admin_client.post("/api/v1/admin/options/sets/office_terms/options", json={
        "value": "alpha",
        "label": "Alpha",
        "display_order": 10,
    })
    assert option.status_code == 201
    option_id = option.get_json()["id"]

    patched = admin_client.patch(f"/api/v1/admin/options/options/{option_id}", json={
        "value": "beta",
        "label": "Beta",
        "active": False,
    })
    assert patched.status_code == 200
    assert patched.get_json()["value"] == "beta"
    assert patched.get_json()["active"] == 0

    listed = admin_client.get("/api/v1/admin/options/sets?include_inactive=1")
    assert listed.status_code == 200
    office = next(row for row in listed.get_json() if row["key"] == "office_terms")
    assert office["options"][0]["value"] == "beta"
    assert office["options"][0]["active"] == 0

    visible = admin_client.get("/api/v1/options/office_terms")
    assert visible.status_code == 200
    assert visible.get_json() == []

    with temp_app.app_context():
        sess = get_session()
        option_set = sess.get(ManagedOptionSet, created.get_json()["id"])
        option_row = sess.get(ManagedOption, option_id)
        assert option_set.label == "Office Terms"
        assert option_row.value == "beta"


# ── Approved emails ──────────────────────────────────────────────────────

def test_add_approved_email(admin_client, temp_app):
    r = admin_client.post(
        "/api/v1/admin/approved-emails",
        json={"email": "contractor@example.com"},
    )
    assert r.status_code in (200, 201)

    with temp_app.app_context():
        sess = get_session()
        row = sess.get(ApprovedEmail, "contractor@example.com")
        assert row is not None


def test_add_approved_email_rejects_bad_input(admin_client):
    r = admin_client.post("/api/v1/admin/approved-emails", json={})
    assert r.status_code == 400


def test_remove_approved_email(admin_client, temp_app):
    # seed
    with temp_app.app_context():
        sess = get_session()
        sess.add(ApprovedEmail(email="bye@example.com"))
        sess.commit()

    r = admin_client.delete("/api/v1/admin/approved-emails/bye@example.com")
    assert r.status_code in (200, 204)

    with temp_app.app_context():
        sess = get_session()
        assert sess.get(ApprovedEmail, "bye@example.com") is None


# ── User role toggle ─────────────────────────────────────────────────────

def test_toggle_user_role(admin_client, temp_app):
    # Regular user (id=1) was seeded by auth_client fixture? No — admin_client
    # only seeds the admin (id=2). Seed a target user.
    with temp_app.app_context():
        sess = get_session()
        sess.add(User(
            id=10, email="promote@x.com", display_name="Pro",
            password_hash="x", role="user",
        ))
        sess.commit()

    r = admin_client.put(
        "/api/v1/admin/users/10/role",
        json={"role": "admin"},
    )
    assert r.status_code == 200

    with temp_app.app_context():
        sess = get_session()
        assert sess.get(User, 10).role == "admin"


def test_role_change_rejects_invalid_role(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(User(
            id=11, email="x@x.com", display_name="X",
            password_hash="x", role="user",
        ))
        sess.commit()

    r = admin_client.put(
        "/api/v1/admin/users/11/role",
        json={"role": "superuser"},
    )
    assert r.status_code == 400


def test_role_change_404_for_missing_user(admin_client):
    r = admin_client.put(
        "/api/v1/admin/users/99999/role",
        json={"role": "admin"},
    )
    assert r.status_code == 404


# ── User deletion ────────────────────────────────────────────────────────

def test_delete_user(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(User(
            id=20, email="delete@x.com", display_name="Del",
            password_hash="x", role="user",
        ))
        sess.commit()

    r = admin_client.delete("/api/v1/admin/users/20")
    assert r.status_code in (200, 204)

    with temp_app.app_context():
        sess = get_session()
        assert sess.get(User, 20) is None


def test_delete_user_is_idempotent_for_missing(admin_client):
    # Route deliberately treats deleting a non-existent user as a no-op
    # success — see admin.py:delete_user.
    r = admin_client.delete("/api/v1/admin/users/99999")
    assert r.status_code == 200
    assert r.get_json() == {"deleted": 99999}


def test_admin_cannot_delete_self(admin_client):
    # admin_client is seeded as id=2 by the fixture.
    r = admin_client.delete("/api/v1/admin/users/2")
    assert r.status_code in (400, 403)


# ── Password reset ───────────────────────────────────────────────────────

def test_password_reset(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(User(
            id=30, email="pw@x.com", display_name="Pw",
            password_hash="old", role="user",
        ))
        sess.commit()

    r = admin_client.put(
        "/api/v1/admin/users/30/reset-password",
        json={"password": "new-pass-1234"},
    )
    assert r.status_code == 200

    with temp_app.app_context():
        sess = get_session()
        # Hash changed and is not the plaintext value.
        u = sess.get(User, 30)
        assert u.password_hash != "old"
        assert u.password_hash != "new-pass-1234"


def test_password_reset_rejects_short_password(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(User(
            id=31, email="short@x.com", display_name="Sh",
            password_hash="x", role="user",
        ))
        sess.commit()

    r = admin_client.put(
        "/api/v1/admin/users/31/reset-password",
        json={"password": "abc"},
    )
    assert r.status_code == 400


# ── Telegram management ──────────────────────────────────────────────────

def test_regenerate_telegram_link_code(admin_client):
    r = admin_client.put("/api/v1/admin/telegram/link-code/regenerate")
    assert r.status_code == 200
    body = r.get_json()
    assert "telegram_link_code" in body
    # 8-byte hex = 16 chars
    assert len(body["telegram_link_code"]) == 16


def test_remove_telegram_chat(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        sess.add(TelegramChatAccess(
            chat_id=12345, display_name="Bot user", is_active=1,
        ))
        sess.commit()

    r = admin_client.delete("/api/v1/admin/telegram/chats/12345")
    assert r.status_code in (200, 204)

    with temp_app.app_context():
        sess = get_session()
        # Either removed entirely or marked inactive — accept both.
        chat = sess.get(TelegramChatAccess, 12345)
        assert chat is None or chat.is_active == 0
