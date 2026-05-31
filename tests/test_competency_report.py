"""Admin-only competency report tests."""

from app.db import get_session
from app.models import Employee, SkillCategory


def _seed_competency_report(admin_client, temp_app):
    with temp_app.app_context():
        sess = get_session()
        emp1 = Employee(display_name="Report Rated", title="CAD Tech", role="drafter", competency_tracked=1)
        emp2 = Employee(display_name="Report Missing", title="Engineer", role="engineer", competency_tracked=1)
        emp3 = Employee(display_name="Report Excluded", title="Principal", role="manager", competency_tracked=0)
        cat1 = SkillCategory(slug="report-cad", name="Report CAD", display_order=1)
        cat2 = SkillCategory(slug="report-setup", name="Report Setup", display_order=2)
        sess.add_all([emp1, emp2, emp3, cat1, cat2])
        sess.commit()
        ids = {
            "emp1": emp1.id,
            "emp2": emp2.id,
            "emp3": emp3.id,
            "cat1": cat1.id,
            "cat2": cat2.id,
        }

    admin_client.post("/api/v1/skills/scores/bulk", json={
        "employee_id": ids["emp1"],
        "source_kind": "preliminary_rating",
        "ratings": [
            {"category_id": ids["cat1"], "score": 2.5, "notes": "needs training"},
            {"category_id": ids["cat2"], "score": 4.0, "notes": "solid"},
        ],
    })
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": ids["emp1"],
        "category_id": ids["cat2"],
        "score": 4.0,
        "source_kind": "official_baseline",
        "notes": "approved",
    })
    admin_client.post("/api/v1/skills/scores", json={
        "employee_id": ids["emp3"],
        "category_id": ids["cat1"],
        "score": 5.0,
        "source_kind": "preliminary_rating",
    })
    return ids


def test_competency_report_json_html_csv(admin_client, temp_app):
    _seed_competency_report(admin_client, temp_app)

    r = admin_client.get("/api/v1/reports/competency")
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["employee_count"] == 2
    assert body["summary"]["category_count"] >= 2
    names = {row["display_name"] for row in body["employees"]}
    assert "Report Rated" in names
    assert "Report Missing" in names
    assert "Report Excluded" not in names
    rated = next(row for row in body["employees"] if row["display_name"] == "Report Rated")
    assert rated["preliminary_count"] == 2
    assert rated["baseline_count"] == 1
    assert rated["low_scores"][0]["score"] == 2.5

    html = admin_client.get("/reports/competency")
    assert html.status_code == 200
    page = html.get_data(as_text=True)
    assert "Competency Report" in page
    assert "Report Rated" in page
    assert "/api/v1/reports/competency.csv" in page

    csv_resp = admin_client.get("/api/v1/reports/competency.csv")
    assert csv_resp.status_code == 200
    csv_text = csv_resp.get_data(as_text=True)
    assert "employee,title,role" in csv_text
    assert "Report Rated" in csv_text
    assert "needs training" in csv_text


def test_competency_report_filters(admin_client, temp_app):
    _seed_competency_report(admin_client, temp_app)

    low = admin_client.get("/api/v1/reports/competency?status=low_scores")
    assert low.status_code == 200
    low_body = low.get_json()
    assert [row["display_name"] for row in low_body["employees"]] == ["Report Rated"]
    assert low_body["summary"]["employee_count"] == 1
    assert low_body["summary"]["low_score_cells"] == 1

    included = admin_client.get("/api/v1/reports/competency?include_untracked=1&q=excluded")
    assert included.status_code == 200
    names = {row["display_name"] for row in included.get_json()["employees"]}
    assert names == {"Report Excluded"}


def test_competency_report_admin_only(auth_client, client):
    assert auth_client.get("/api/v1/reports/competency").status_code == 403
    assert auth_client.get("/api/v1/reports/competency.csv").status_code == 403
    assert auth_client.get("/reports/competency", follow_redirects=False).status_code == 302
    with client.session_transaction() as session_data:
        session_data.clear()
    assert client.get("/api/v1/reports/competency").status_code == 401


def test_competency_report_center_and_presets(admin_client, auth_client):
    with admin_client.session_transaction() as session_data:
        session_data["user_id"] = 2
        session_data["user_name"] = "Admin User"
        session_data["user_role"] = "admin"
    page = admin_client.get("/reports").get_data(as_text=True)
    assert "Competency Reports" in page
    assert "/reports/competency" in page

    with auth_client.session_transaction() as session_data:
        session_data["user_id"] = 1
        session_data["user_name"] = "Tester"
        session_data["user_role"] = "user"
    denied = auth_client.post("/api/v1/reports/presets", json={
        "name": "Denied Competency",
        "surface": "competency",
        "filters": {"status": "needs_baseline"},
    })
    assert denied.status_code == 403

    with admin_client.session_transaction() as session_data:
        session_data["user_id"] = 2
        session_data["user_name"] = "Admin User"
        session_data["user_role"] = "admin"
    created = admin_client.post("/api/v1/reports/presets", json={
        "name": "Needs Baseline",
        "surface": "competency",
        "filters": {"status": "needs_baseline"},
    })
    assert created.status_code == 201
    preset_id = created.get_json()["id"]
    report = admin_client.get(f"/api/v1/reports/competency?preset={preset_id}")
    assert report.status_code == 200
    assert report.get_json()["selected_preset"]["name"] == "Needs Baseline"
