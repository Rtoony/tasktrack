"""In-process tests using the Flask test client and an isolated temp DB.

These bypass the running gunicorn instance and the live DB, exercising
the same routes via the Flask test client against a fresh SQLite per
test (schema built from SQLAlchemy metadata in conftest).

Re-enabled in Phase 1D-2j once Alembic owned schema and the legacy
runtime mutation that tripped fresh-init was deleted.
"""


def test_healthz_via_test_client(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.data == b"ok"


def test_login_form_renders_via_test_client(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"Sign in" in r.data
    assert b"Submission Forms" in r.data
    assert b"approved email" in r.data


def test_root_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_api_blocks_unauthenticated(client):
    r = client.get("/api/v1/work_tasks", follow_redirects=False)
    assert r.status_code in (401, 302)


def test_admin_blocks_non_admin(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code in (401, 302, 403)


def test_register_page_explains_approval(client):
    r = client.get("/register")
    assert r.status_code == 200
    assert b"already approved by an admin" in r.data
    assert b"Submission Forms" in r.data


def test_register_rejects_unapproved_email(client):
    r = client.post(
        "/register",
        data={
            "email": "stranger@example.com",
            "name": "Stranger",
            "password": "test1234",
        },
    )
    assert r.status_code == 200
    assert b"not on the approved list" in r.data


def test_legacy_api_path_redirects_to_v1(client):
    r = client.get("/api/work_tasks", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/api/v1/work_tasks")


def test_legacy_submit_path_redirects_to_intake(client):
    r = client.get("/submit", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/intake")


def test_capability_intake_returns_404(client):
    r = client.get("/intake/capability")
    assert r.status_code == 404



def test_printable_intake_packet_renders_for_paper_and_remarkable(client):
    r = client.get("/intake/printable")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Printable Intake Forms" in html
    assert "Print / Save PDF" in html
    assert "OCR intake block" in html
    assert "TT-PROJECT-WORK-REQUEST" in html
    assert "TT-CAD-ISSUE-REQUEST" in html
    assert "TT-TRAINING-IMPROVEMENT-REQUEST" in html
    assert "TARGET_TABLE=project_work_tasks" in html
    assert "/capture/ocr" in html
    assert "reMarkable layout" in html

    single = client.get("/intake/printable?form=cad-development&layout=remarkable")
    assert single.status_code == 200
    single_html = single.get_data(as_text=True)
    assert "TT-CAD-ISSUE-REQUEST" in single_html
    assert "TT-PROJECT-WORK-REQUEST" not in single_html
    assert 'class="page remarkable"' in single_html

    assert client.get("/intake/printable?form=missing").status_code == 404
