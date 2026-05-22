"""End-to-end tests for the master-list importer.

Builds tiny in-memory fixtures (XLSX + KMZ) and runs
`scripts.import_projects_from_master.run_import` against a temp DB.
Covers:

- Excel rows land as `projects` rows with the right fields normalized.
- KMZ placemarks land as `project_sites` rows linked to their parents.
- Multi-pin projects keep every pin and pick yellow as primary.
- KMZ-only orphans (no Excel row) get an auto-created blank project.
- Legend placemarks (YELLOW=/RED=/...) are skipped.
- A second run against the same sources is a no-op (idempotent).
"""
from __future__ import annotations

import io
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.db import get_session
from app.models import Project, ProjectSite

# The import script lives outside the app package — add scripts/ dir
# explicitly so we can import it. Mirror the script's own sys.path
# stanza so the import works whether or not pytest is invoked from the
# repo root.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import import_projects_from_master as importer  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_xlsx(tmp_path: Path) -> Path:
    """Produce an XLSX shaped like the firm's Master List - Numeric.

    Header rows live on rows 1-4 (matching the real file); data rows
    start on row 5.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Project List", None, None, None, None, None, None, datetime(2026, 4, 28)])
    ws.append(["Brelje & Race Consulting Engineers"])
    ws.append([])
    ws.append(["Project", "Name", "Status", "Start\nDate", "Dormant\nDate",
               "Client\nName", "Principal", "Component"])
    # Data rows. Project numbers cover the three Excel quirks:
    #   - float with two decimals (1234.05)
    #   - float displayed as one decimal but really two (209.1)
    #   - bare integer (1500)
    ws.append([1234.05, "Bridge replacement", "Active",
               datetime(2025, 3, 1), None,
               "City of Springfield", "Long, David", "Site Improvement Plans"])
    ws.append([209.1, "Tank upgrade", "Dormant",
               datetime(2007, 4, 13), datetime(2009, 7, 31),
               "Forestville Water District", "Long, David", "Water Distribution"])
    ws.append([1500, "Survey baseline", "Active",
               None, None, "OSL Construction", "Race, Larry", "Topographic Mapping"])
    out = tmp_path / "master.xlsx"
    wb.save(out)
    return out


def _make_kmz(tmp_path: Path) -> Path:
    """Produce a KMZ with:

      - 4 legend placemarks at the top (must be filtered out)
      - 1234.05 with TWO sites (one yellow, one red — multi-site)
      - 209.10 with one yellow site (note: KMZ name written as "209.1"
        so the normalizer earns its keep)
      - 9999.00 with one yellow site (KMZ-only orphan — no Excel row)
    """
    style_block = """
      <Style id="yel"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png</href></Icon></IconStyle></Style>
      <Style id="red"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/pushpin/red-pushpin.png</href></Icon></IconStyle></Style>
      <Style id="grn"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/pushpin/grn-pushpin.png</href></Icon></IconStyle></Style>
      <Style id="blu"><IconStyle><Icon><href>http://maps.google.com/mapfiles/kml/pushpin/blue-pushpin.png</href></Icon></IconStyle></Style>
    """

    def _pm(name, lng, lat, style):
        return (
            f'<Placemark><name>{name}</name>'
            f'<styleUrl>#{style}</styleUrl>'
            f'<Point><coordinates>{lng},{lat},0</coordinates></Point>'
            f'</Placemark>'
        )

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <name>Project Locator.kmz</name>
  {style_block}
  <Folder>
    <name>Project Locator</name>
    {_pm("YELLOW=Project Input Form Placement", -122.79, 38.51, "yel")}
    {_pm("RED=Archived PDF", -122.79, 38.51, "red")}
    {_pm("GREEN=Topo", -122.79, 38.51, "grn")}
    {_pm("BLUE=Archived PDF-survey", -122.79, 38.51, "blu")}
  </Folder>
  <Folder>
    <name>1000 - 1499</name>
    {_pm("1234.05", -122.81, 38.55, "yel")}
    {_pm("1234.05", -122.50, 38.40, "red")}
  </Folder>
  <Folder>
    <name>1 - 499</name>
    {_pm("209.1", -123.01, 38.79, "yel")}
  </Folder>
  <Folder>
    <name>8500 - 8999</name>
    {_pm("9999.00", -122.60, 38.45, "yel")}
  </Folder>
</Document></kml>
"""
    out = tmp_path / "locator.kmz"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml)
    return out


def _db_url(temp_app) -> str:
    """The temp_app fixture stashes its sqlite path on the Flask config
    rather than mutating the module-level `app.db.DB_PATH`."""
    return f"sqlite:///{temp_app.config['DB_PATH']}"


# ── Tests ─────────────────────────────────────────────────────────────────


def test_run_import_dry_run_does_not_touch_db(tmp_path, temp_app):
    xlsx = _make_xlsx(tmp_path)
    kmz = _make_kmz(tmp_path)
    stats = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=True)
    assert stats["excel_rows"] == 3
    # 4 pins kept (one for each non-legend placemark); 3 unique numbers
    assert stats["kmz_pins_total"] == 4
    assert stats["kmz_unique_numbers"] == 3
    assert stats["kmz_only_orphans"] == 1  # 9999.00 has no Excel row

    with temp_app.app_context():
        sess = get_session()
        assert sess.scalar(select(Project).where(Project.project_number == "1234.05")) is None


def test_run_import_lands_projects_and_sites(tmp_path, temp_app):
    xlsx = _make_xlsx(tmp_path)
    kmz = _make_kmz(tmp_path)
    stats = importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    assert stats["projects_upserted"] == 3
    assert stats["orphan_projects_created"] == 1
    assert stats["sites_inserted"] == 4

    with temp_app.app_context():
        sess = get_session()
        all_projects = sess.scalars(select(Project)).all()
        assert len(all_projects) == 4  # 3 Excel + 1 orphan

        # Excel-driven fields landed on 1234.05
        bridge = sess.scalar(
            select(Project).where(Project.project_number == "1234.05")
        )
        assert bridge.name == "Bridge replacement"
        assert bridge.client == "City of Springfield"
        assert bridge.principal == "Long, David"
        assert bridge.component == "Site Improvement Plans"
        assert bridge.start_date == "2025-03-01"
        assert bridge.display_status == "active"

        # 209.1 normalized to 209.10; date columns landed as ISO strings
        tank = sess.scalar(
            select(Project).where(Project.project_number == "209.10")
        )
        assert tank is not None, "209.1 should normalize to 209.10"
        assert tank.dormant_date == "2009-07-31"
        assert tank.display_status == "dormant"

        # 1500 padded to 1500.00
        survey = sess.scalar(
            select(Project).where(Project.project_number == "1500.00")
        )
        assert survey is not None
        assert survey.component == "Topographic Mapping"


def test_multi_site_picks_yellow_as_primary(tmp_path, temp_app):
    xlsx = _make_xlsx(tmp_path)
    kmz = _make_kmz(tmp_path)
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    with temp_app.app_context():
        sess = get_session()
        proj = sess.scalar(select(Project).where(Project.project_number == "1234.05"))
        sites = sess.scalars(
            select(ProjectSite).where(ProjectSite.project_id == proj.id)
        ).all()
        assert len(sites) == 2
        colors = {s.pin_color: s for s in sites}
        assert "yellow" in colors and "red" in colors
        assert colors["yellow"].is_primary == 1
        assert colors["red"].is_primary == 0
        # The project's legacy lat/lng mirror the primary (yellow) site.
        assert abs(proj.lat - 38.55) < 1e-6
        assert abs(proj.lng - (-122.81)) < 1e-6


def test_kmz_only_orphan_is_auto_created(tmp_path, temp_app):
    xlsx = _make_xlsx(tmp_path)
    kmz = _make_kmz(tmp_path)
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    with temp_app.app_context():
        sess = get_session()
        orphan = sess.scalar(
            select(Project).where(Project.project_number == "9999.00")
        )
        assert orphan is not None
        assert orphan.name == ""  # no Excel row -> blank
        assert "Auto-created" in orphan.notes
        sites = sess.scalars(
            select(ProjectSite).where(ProjectSite.project_id == orphan.id)
        ).all()
        assert len(sites) == 1


def test_legend_placemarks_skipped(tmp_path, temp_app):
    xlsx = _make_xlsx(tmp_path)
    kmz = _make_kmz(tmp_path)
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)

    with temp_app.app_context():
        sess = get_session()
        legend_hits = sess.scalars(
            select(Project).where(Project.project_number.like("YELLOW%"))
        ).all()
        assert legend_hits == []
        # And no project_sites should reference one either.
        assert sess.query(ProjectSite).count() == 4


def test_idempotent_second_run(tmp_path, temp_app):
    xlsx = _make_xlsx(tmp_path)
    kmz = _make_kmz(tmp_path)
    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    with temp_app.app_context():
        sess = get_session()
        before_projects = sess.query(Project).count()
        before_sites = sess.query(ProjectSite).count()

    importer.run_import(xlsx, kmz, db_url=_db_url(temp_app), dry_run=False)
    with temp_app.app_context():
        sess = get_session()
        assert sess.query(Project).count() == before_projects
        assert sess.query(ProjectSite).count() == before_sites


def test_normalize_project_number_edge_cases():
    n = importer.normalize_project_number
    assert n(1234) == "1234.00"
    assert n(1234.05) == "1234.05"
    assert n(209.1) == "209.10"
    assert n("1014") == "1014.00"
    assert n("209.1") == "209.10"
    assert n("1014.05") == "1014.05"
    # Oddball suffixes pass through verbatim — they live outside the
    # ####.## lane and will not match Excel rows.
    assert n("4683.00-SLS - 19") == "4683.00-SLS - 19"
    assert n("") == ""
    assert n(None) == ""
