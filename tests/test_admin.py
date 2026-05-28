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
from app.db import get_session
from app.models import ApprovedEmail, TelegramChatAccess, User

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
    assert "Pages & Reports" in html
    assert "Report Center" in html
    assert "Today Brief" in html
    assert "Portfolio Reports" in html
    assert "At-Risk Queue" in html
    assert "At-Risk CSV" in html
    assert "Project One-Pager" in html
    assert "Meeting Packet Batch" in html
    assert "Weekly Review" in html
    assert "Submission Forms" in html
    assert "/reports/today" in html
    assert "/reports/meetings?days=14&limit=12" in html




def test_admin_api_endpoint_returns_401_for_anonymous(client):
    r = client.post("/api/v1/admin/approved-emails", json={"email": "x@y.com"})
    assert r.status_code == 401


def test_admin_api_endpoint_returns_403_for_regular_user(auth_client):
    r = auth_client.post("/api/v1/admin/approved-emails", json={"email": "x@y.com"})
    assert r.status_code == 403


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
