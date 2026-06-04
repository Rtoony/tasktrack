"""Static configuration: per-table field schemas, intake form configs, admin workflow metadata.

Lives at module scope (not in `create_app`) because it's pure data — the
telegram bot, tests, and routes all import these names directly.
"""

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
            "starter_note",
            "clarifications_needed",
            "software",
            "needs_review",
            "source",
            "ai_raw_input",
            "ai_model",
            "project_number",
            "project_id",
        ],
        "required": ["title"],
        "label": "CAD Dev Task",
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
            "scheduled_completion_at",
            "time_required_minutes",
            "notes",
            "scope_notes",
            "progress_notes",
            "confirmation_notes",
            "completion_notes",
            "needs_review",
            "source",
            "ai_raw_input",
            "ai_model",
            "project_id",
            "engineer_id",
        ],
        "required": ["project_name", "title", "project_number", "task_description"],
        "label": "Project Task",
        "status_flow": ["Not Started", "In Progress", "Pending Confirmation", "Complete"],
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
            "needs_review",
            "source",
            "ai_raw_input",
            "ai_model",
            "project_number",
            "project_id",
            "trainee_ids",
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
            "project_number",
            "project_id",
            "person_id",
            "estimated_time_loss_minutes",
            "immediate_solution",
            "skill_category_id",
            "person_ids",
        ],
        # Phase-5.5: an incident may involve zero people (process /
        # equipment), one person, or many — person_name is no longer
        # required. issue_description is still the one mandatory field.
        "required": ["issue_description"],
        "label": "Incident Report",
        "status_flow": ["Observed", "Coaching Planned", "Training Scheduled", "Monitoring", "Closed"],
    },
    "inbox_items": {
        "fields": [
            "title",
            "body",
            "source",
            "source_ref",
            "status",
            "priority",
            "due_date",
            "promoted_to_table",
            "promoted_to_id",
        ],
        "required": ["title"],
        "label": "Triage Item",
        "status_flow": ["New", "In Progress", "Done", "Archived"],
    },
    "personal_items": {
        "fields": [
            "title",
            "category",
            "body",
            "priority",
            "status",
            "due_date",
            "needs_review",
            "source",
            "source_ref",
        ],
        "required": ["title", "category"],
        "label": "Internal Item",
        "status_flow": ["New", "In Progress", "Done", "Archived"],
    },
    "feedback_items": {
        "fields": [
            "title",
            "body",
            "feedback_type",
            "priority",
            "status",
            "page_url",
            "tab",
            "component_label",
            "context_json",
            "tags",
            "resolution_notes",
            "resolution_metadata_json",
            "source",
        ],
        "required": ["title"],
        "label": "Feedback",
        "status_flow": ["New", "Triaged", "Planned", "In Progress", "Needs Info", "Ready to Test", "Fixed", "Accepted", "Closed", "Won't Fix", "Archived"],
    },
    "calendar_events": {
        "fields": [
            "event_type",
            "title",
            "description",
            "start_at",
            "end_at",
            "all_day",
            "status",
            "project_id",
            "project_number",
            "related_table",
            "related_id",
            "reminder_date",
            "location",
            "visibility",
        ],
        "required": ["title", "start_at"],
        "label": "Calendar Event",
        "status_flow": ["scheduled", "tentative", "done", "cancelled"],
    },
}

INTERNAL_ITEM_CATEGORIES = ["Follow-up", "Meetings", "Office", "Assets"]
CALENDAR_EVENT_TYPES = [
    "meeting", "milestone", "deadline", "review",
    "task_due", "prep", "reminder", "other",
]
CALENDAR_VISIBILITIES = ["internal", "private", "shared"]

SIMPLE_SUBMISSION_CONFIGS = {
    "cad-development": {
        "table": "work_tasks",
        "source_name": "CAD Request Form",
        "page_title": "CAD Request Submission",
        "heading": "Submit a CAD Request",
        "intro": "Use this form when a CAD-related change, update, fix, or follow-up item should be logged for managers to assign.",
        "submit_label": "Submit CAD Request",
        "success_noun": "CAD request",
        "source": "web-form",
        "needs_review": True,
        "form_id": "TT-WEB-CAD-REQUEST",
        "audience": "Send to staff when CAD fixes, drafting support, standards updates, or file/sheet issues need tracking.",
        "fields": [
            {"name": "title", "label": "Task Title", "type": "text", "required": True, "placeholder": "Short name for the request"},
            {"name": "requested_by", "label": "Your Name", "type": "text", "required": True, "placeholder": "Jane Smith"},
            {"name": "cad_skill_area", "label": "CAD Skill Area", "type": "text", "placeholder": "Detailing, modeling, standards, templates"},
            {"name": "description", "label": "Requested Change", "type": "textarea", "required": True, "placeholder": "What should be changed or addressed?"},
            {"name": "request_reference", "label": "Context / Follow-up Reference", "type": "textarea", "placeholder": "Who was involved and what context should stay with this request?"},
            {"name": "due_date", "label": "Needed By", "type": "date"},
        ],
    },
    "project-request": {
        "table": "project_work_tasks",
        "source_name": "Project Work Request Form",
        "page_title": "Project Work Request",
        "heading": "Submit a Project Work Request",
        "intro": "Use this when a project-specific task, deliverable, review item, agency response, or management follow-up needs to become tracked work.",
        "submit_label": "Submit Project Request",
        "success_noun": "project request",
        "source": "web-form",
        "needs_review": True,
        "form_id": "TT-WEB-PROJECT-WORK-REQUEST",
        "audience": "Send to engineers, PMs, survey, or anyone asking for project work.",
        "fields": [
            {"name": "title", "label": "Request Title", "type": "text", "required": True, "placeholder": "Short name for the work request"},
            {"name": "project_number", "label": "Project Number", "type": "text", "required": True, "placeholder": "1234.56"},
            {"name": "project_name", "label": "Project Name / Site", "type": "text", "required": True, "placeholder": "Project name, site, or client reference"},
            {"name": "engineer", "label": "Requested By / Responsible Person", "type": "text", "placeholder": "Name of requester, engineer, reviewer, or PM"},
            {"name": "billing_phase", "label": "Billing Phase", "type": "text", "placeholder": "Optional, e.g. 01"},
            {"name": "task_description", "label": "What needs to be done?", "type": "textarea", "required": True, "placeholder": "Describe the requested deliverable, decision, correction, or follow-up."},
            {"name": "due_at", "label": "Needed By", "type": "datetime-local"},
            {"name": "scheduled_completion_at", "label": "Scheduled Completion", "type": "datetime-local"},
            {"name": "time_required_minutes", "label": "Time Required (minutes)", "type": "number", "placeholder": "30"},
            {"name": "priority", "label": "Priority", "type": "select", "options": ["Low", "Medium", "High"], "default": "Medium"},
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
        "source": "web-form",
        "needs_review": True,
        "form_id": "TT-WEB-TRAINING-REQUEST",
        "audience": "Send to staff or managers when a training need, repeated issue, or process improvement should be tracked.",
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
    "general-follow-up": {
        "table": "personal_items",
        "source_name": "General Follow-Up Form",
        "page_title": "General Follow-Up Request",
        "heading": "Submit a General Follow-Up",
        "intro": "Use this for office follow-ups, management questions, meeting action items, equipment notes, or anything that should not stay buried in email.",
        "submit_label": "Submit Follow-Up",
        "success_noun": "follow-up request",
        "source": "web-form",
        "needs_review": True,
        "form_id": "TT-WEB-GENERAL-FOLLOW-UP",
        "audience": "Send when the request is real but does not clearly belong to CAD, project work, training, or incident reporting.",
        "fields": [
            {"name": "title", "label": "Follow-Up Title", "type": "text", "required": True, "placeholder": "Short name for the follow-up"},
            {"name": "category", "label": "Category", "type": "select", "options": ["Follow-up", "Meetings", "Office", "Assets"], "default": "Follow-up", "required": True},
            {"name": "body", "label": "Details / Context", "type": "textarea", "placeholder": "What needs follow-up? Who is involved? What decision, answer, or next step is needed?"},
            {"name": "priority", "label": "Priority", "type": "select", "options": ["Low", "Medium", "High"], "default": "Medium"},
            {"name": "due_date", "label": "Needed By", "type": "date"},
            {"name": "source_ref", "label": "Reference / Meeting / Email", "type": "text", "placeholder": "Optional source reference"},
        ],
    },
    "capability": {
        # Retained for backward-compat with the SIMPLE_SUBMISSION_CONFIGS
        # contract (telegram_bot.py imports this name). The /intake/
        # capability URL is intentionally 404'd in app/__init__.py
        # because anonymous HR-data submissions were retired
        # 2026-04-26. See `incident` below for the auth-gated successor.
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
    "incident": {
        # Phase-5.5 successor to the retired anonymous capability form.
        # Mounted at /intake/incident with @login_required so HR-sensitive
        # data still goes through authenticated submitters. Supports 0,
        # 1, or many people via the comma-separated "people_involved"
        # field — the backend enrich_with_fks splits and resolves names
        # against the Employees registry, writing both person_name (text,
        # preserved) and person_ids (JSON list of matched ids).
        "table": "personnel_issues",
        "source_name": "Incident Report Form",
        "page_title": "Incident Report",
        "heading": "Report an Incident",
        "intro": "Log a CAD process gap, capability shortfall, or work-related incident. People involved is optional — leave blank for process or equipment incidents.",
        "submit_label": "Submit Incident Report",
        "success_noun": "incident report",
        # Marker for the hub UI to display a lock icon. Route enforces
        # @login_required separately; this string is for the template.
        "auth_required": True,
        "fields": [
            {"name": "issue_description", "label": "Incident Summary", "type": "textarea", "required": True, "placeholder": "Brief factual summary — what happened?"},
            {"name": "person_name", "label": "People Involved (comma-separated, optional)", "type": "text", "placeholder": "Alice Smith, Bob Jones — or leave blank for 0-person incidents"},
            {"name": "observed_by", "label": "Reported By", "type": "text", "placeholder": "Your name (optional)"},
            {"name": "incident_context", "label": "Context", "type": "textarea", "placeholder": "What work or situation exposed this?"},
            {"name": "cad_skill_area", "label": "Skill Area", "type": "text", "placeholder": "Detailing, modeling, standards, drainage, etc."},
            {"name": "immediate_solution", "label": "Immediate Fix Applied", "type": "textarea", "placeholder": "What did you do in the moment to unblock the work?"},
            {"name": "recommended_training", "label": "Recommended Training / Follow-Up", "type": "textarea", "placeholder": "What coaching, review, or training should happen next?"},
            {"name": "severity", "label": "Severity", "type": "select", "options": ["Low", "Medium", "High", "Critical"], "default": "Medium"},
            {"name": "estimated_time_loss_minutes", "label": "Estimated Time Lost (minutes)", "type": "number", "placeholder": "0"},
            {"name": "project_number", "label": "Project Number (optional)", "type": "text", "placeholder": "1234.56"},
        ],
    },
}

ADMIN_WORKFLOW_VIEWS = {
    "project": {
        "title": "Project Tasks",
        "subtitle": "Manage project-linked execution work with project numbers, billing phase, engineer ownership, and due timing.",
    },
    "work": {
        "title": "CAD Dev",
        "subtitle": "Track requested CAD changes, the discipline involved, and the follow-up context behind the work.",
    },
    "training": {
        "title": "Training",
        "subtitle": "Plan and track targeted training work by staff member, skill area, goals, and follow-up context.",
    },
    "personnel": {
        "title": "Capabilities",
        "subtitle": "Record observed CAD capability gaps over time so coaching and training needs are visible and traceable.",
    },
    "triage": {
        "title": "Triage",
        "subtitle": "Quick captures from Telegram, voice memos, paperless, or any Nexus app — triage from here into the right tracker, or leave as an internal follow-up.",
    },
    "personal_husband": {
        "title": "Follow-up",
        "subtitle": "Internal follow-up items that do not belong in a project, CAD, training, or capability queue yet.",
    },
    "personal_father": {
        "title": "Meetings",
        "subtitle": "Meeting prep, management follow-ups, and time-bound coordination notes.",
    },
    "personal_house": {
        "title": "Office",
        "subtitle": "Office operations, workspace upkeep, and non-project administrative work.",
    },
    "personal_cars": {
        "title": "Assets",
        "subtitle": "Assets, equipment, tools, and other operational upkeep items.",
    },
}


# ── CAD/GIS Competency v2 ─────────────────────────────────────────────────
#
# Task-level rubric for the supervision-anchored 0/1/2/3 competency system.
# The seed runs the first time `/api/v1/skills/categories` is hit if the
# table is empty; admins can add / disable categories afterward without
# re-seeding.

CAD_COMPETENCY_CATALOG = [
    {
        "slug": "computer-windows-literacy",
        "name": "Computer & Windows Literacy",
        "description": "Basic computer, Windows, Office, PDF, file, and troubleshooting fluency.",
        "display_order": 10,
        "tasks": [
            ("file-folder-network-drive-management", "File, folder & network-drive management", "C", "Navigates project/network paths unaided; never loses files."),
            ("file-naming-version-discipline", "File naming & version discipline", "C", "Follows convention unprompted; clean revisions."),
            ("email-calendar-office-basics", "Email, calendar & Office basics", "C", "Outlook fluent; Word/Excel edits + simple formulas solo."),
            ("pdf-tools-bluebeam-acrobat", "PDF tools (Bluebeam / Acrobat)", "C", "Create / combine / split / markup / compare solo."),
            ("os-fluency", "OS fluency", "C", "Shortcuts, multi-monitor, cross-app copy/paste, screenshots."),
            ("basic-troubleshooting-it-escalation", "Basic troubleshooting & IT escalation", "C", "Fixes drives/printers; knows what to fix vs. call IT."),
        ],
    },
    {
        "slug": "autocad-core",
        "name": "AutoCAD Core",
        "description": "Core AutoCAD production skill, references, plotting, cleanup, and sheet setup.",
        "display_order": 20,
        "tasks": [
            ("command-line-interface-fluency", "Command-line & interface fluency", "C", "Commands/aliases from memory; rarely hunts the ribbon."),
            ("precision-drafting", "Precision drafting", "C", "Osnaps, coordinate entry, units correct every time."),
            ("layer-management", "Layer management", "C", "Create/set/freeze/isolate; uses layer states."),
            ("core-draw-modify-commands", "Core draw & modify commands", "C", "Full modify suite fast and correct."),
            ("blocks-dynamic-blocks", "Blocks & dynamic blocks", "C", "Insert/create/edit + attributes; builds dynamic params."),
            ("annotation-text-dims-hatch-fields", "Annotation (text, dims, hatch, fields)", "C", "Styled, associative, clean; uses fields."),
            ("xref-management", "Xref management", "C", "Attach/path/clip/bind without breaking references."),
            ("layouts-viewports-annotation-scaling", "Layouts, viewports & annotation scaling", "C", "Viewport scales & annotative objects correct."),
            ("plotting-pdf-output", "Plotting & PDF output", "C", "Page setups + CTB/STB; consistent output."),
            ("sheet-set-manager", "Sheet Set Manager", "E", "Builds/maintains sheet sets with fields."),
            ("drawing-cleanup-repair", "Drawing cleanup & repair", "C", "purge/audit/recover/overkill to healthy files."),
        ],
    },
    {
        "slug": "cad-standards-drawing-discipline",
        "name": "CAD Standards & Drawing Discipline",
        "description": "Firm standards, templates, title blocks, naming, styles, and drawing organization.",
        "display_order": 30,
        "tasks": [
            ("company-layer-standard-compliance", "Company layer standard compliance", "C", "Matches the standard without being told."),
            ("template-title-block-usage", "Template & title-block usage", "C", "Right template; fills it correctly."),
            ("text-dim-linetype-style-compliance", "Text / dim / linetype style compliance", "C", "Company styles, not ad-hoc overrides."),
            ("file-naming-drawing-organization", "File naming & drawing organization", "C", "Folder structure & names match firm convention."),
        ],
    },
    {
        "slug": "civil-3d",
        "name": "Civil 3D",
        "description": "Civil 3D points, surfaces, alignments, profiles, grading, networks, DREFs, and styles.",
        "display_order": 40,
        "tasks": [
            ("points-point-groups-survey-import", "Points, point groups & survey import", "C", "Imports & manages point-group display solo."),
            ("surfaces", "Surfaces", "C", "Build/edit, style, analyze, troubleshoot."),
            ("alignments", "Alignments", "C", "Create/edit, criteria, labels."),
            ("profiles-profile-views", "Profiles & profile views", "C", "Surface + layout profiles, views, bands."),
            ("corridors", "Corridors", "E", "Build, edit regions/targets/frequency; rebuilds clean."),
            ("assemblies-subassemblies", "Assemblies & subassemblies", "E", "Assemble/edit params; point/link/shape codes."),
            ("grading-feature-lines-grading-groups", "Grading (feature lines, grading groups)", "C", "Feature-line edits, criteria, daylighting."),
            ("pipe-networks-gravity", "Pipe networks (gravity)", "E", "Layout, edit structures, interference checks."),
            ("pressure-networks", "Pressure networks", "E", "Models pressure networks where applicable."),
            ("parcels", "Parcels", "E", "Create/edit parcels & labels."),
            ("sample-lines-sections", "Sample lines & sections", "E", "Sample-line groups, section views, sheets."),
            ("data-shortcuts-references", "Data shortcuts / references", "C", "Create/reference/repair paths; no broken DREFs."),
            ("object-label-styles", "Object & label styles", "C", "Manages styles; keeps template clean; no hand overrides."),
        ],
    },
    {
        "slug": "plan-production-deliverables",
        "name": "Plan Production & Deliverables",
        "description": "Plan sheets, details, callouts, legends, revisions, final PDFs, and deliverable consistency.",
        "display_order": 50,
        "tasks": [
            ("plan-profile-sheet-production", "Plan & profile sheet production", "C", "Clean, complete plan/profile sheets."),
            ("grading-utility-plan-sheets", "Grading / utility plan sheets", "C", "Readable, labeled, standard-compliant."),
            ("details-detail-management", "Details & detail management", "C", "Place/edit; keeps detail library tidy."),
            ("sheet-consistency-match-lines-callouts", "Sheet consistency, match lines, callouts", "C", "Sheets agree; lines & callouts align."),
            ("legends-scales-north-arrows-revisions-final-pdf", "Legends, scales, north arrows, revisions, final PDF", "C", "Submission-ready, consistent, correct scale."),
        ],
    },
    {
        "slug": "gis-other-software",
        "name": "GIS & Other Software",
        "description": "GIS, coordinate systems, projections, geospatial import/export, and specialty tools.",
        "display_order": 60,
        "tasks": [
            ("coordinate-systems-projections", "Coordinate systems & projections", "E", "State plane / EPSG / datums — set & convert."),
            ("arcgis-qgis-basics", "ArcGIS / QGIS basics", "E", "Load layers, basic edits/queries, simple maps."),
            ("geospatial-import-export-georeferencing", "Geospatial import/export & georeferencing", "E", "Move data CAD <-> GIS; georeference imagery/scans."),
            ("autocad-map-specialty-tools", "AutoCAD Map / specialty tools", "E", "Firm-specific tools as applicable."),
        ],
    },
]

SKILL_CATEGORY_DEFAULTS = [
    {
        "slug": group["slug"],
        "name": group["name"],
        "description": group["description"],
        "display_order": group["display_order"],
    }
    for group in CAD_COMPETENCY_CATALOG
]


# Dimension templates for the CAD/GIS 0-3 task-level competency rollup.
# Dimensions are now concrete tasks. C = core, E = extended/N/A-friendly.
SKILL_DIMENSION_DEFAULTS = {
    group["slug"]: [
        {"slug": slug, "name": name, "weight": 1.0, "tier": tier, "anchor": anchor}
        for slug, name, tier, anchor in group["tasks"]
    ]
    for group in CAD_COMPETENCY_CATALOG
}
SKILL_DIMENSION_DEFAULTS["default"] = [
    {"slug": "observed-readiness", "name": "Observed readiness", "weight": 1.0, "tier": "C", "anchor": "Observed supervision level for this task."},
]

COMPETENCY_LEVELS = [
    {"score": 0, "label": "Can't yet", "decision": "Training target"},
    {"score": 1, "label": "Supervised", "decision": "Assign, but review output"},
    {"score": 2, "label": "Independent", "decision": "Assign freely"},
    {"score": 3, "label": "Go-to / teaches", "decision": "Assign + can train others"},
]



# ── Cross-tracker bridges (Phase 3) ────────────────────────────────────────
#
# Declarative routes that carry data when promoting a source record into a
# target tracker. Shape:
#
#   BRIDGE_MAP[src_table][tgt_table] = {
#       "carry":           {src_field: tgt_field, ...},  # column rename map
#       "defaults":        {tgt_field: value, ...},      # extra constants
#       "title_template":  "Some {src_field} string",    # optional
#       "title_field":     "title",                       # where to put it
#       "required_overrides": ["field1", ...],            # ask UI to prompt
#       "label":           "Open follow-up CAD task",     # for UI dropdown
#   }
#
# The bridge service validates every target field referenced here against
# ALLOWED_TABLES at app boot (_check_bridge_map_fields in app/__init__.py),
# so a typo in this map fails loudly the first time the app starts.

BRIDGE_MAP = {
    "personnel_issues": {
        "training_tasks": {
            "label": "Schedule training for this",
            "carry": {
                "person_name":          "trainees",
                "recommended_training": "training_goals",
                "cad_skill_area":       "skill_area",
                "issue_description":    "additional_context",
                "project_number":       "project_number",
                "project_id":           "project_id",
            },
            "defaults": {"source": "bridge"},
            "title_template": "Training: {person_name}",
            "title_field":    "title",
            "required_overrides": [],
        },
        "work_tasks": {
            "label": "Open follow-up CAD task",
            "carry": {
                "issue_description": "description",
                "cad_skill_area":    "cad_skill_area",
                "project_number":    "project_number",
                "project_id":        "project_id",
            },
            "defaults": {"source": "bridge"},
            "title_template": "Follow-up: {person_name}",
            "title_field":    "title",
            "required_overrides": [],
        },
    },
    "work_tasks": {
        "personnel_issues": {
            "label": "Log a capability gap from this task",
            "carry": {
                "description":    "issue_description",
                "cad_skill_area": "cad_skill_area",
                "project_number": "project_number",
                "project_id":     "project_id",
            },
            # personnel_issues has no `source` column — leave defaults empty.
            "defaults": {},
            # personnel_issues requires person_name; UI must prompt.
            "required_overrides": ["person_name"],
        },
    },
    "project_work_tasks": {
        "personnel_issues": {
            "label": "Log a capability gap from this task",
            "carry": {
                "task_description": "issue_description",
                "engineer":         "person_name",
                "engineer_id":      "person_id",
                "project_number":   "project_number",
                "project_id":       "project_id",
            },
            "defaults": {},
            # engineer text may be blank — UI prompts if person_name lands empty.
            "required_overrides": [],
        },
    },
    "training_tasks": {
        "personnel_issues": {
            "label": "Log a capability gap from this training",
            "carry": {
                "trainees":         "person_name",
                "skill_area":       "cad_skill_area",
                "training_goals":   "recommended_training",
                "project_number":   "project_number",
                "project_id":       "project_id",
            },
            "defaults": {},
            "required_overrides": [],
        },
    },
}

