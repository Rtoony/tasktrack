# TaskTrack — Roadmap

Private internal operations tracker on Nexus. Near-term focus is daily
work coordination, project-linked follow-up, internal calendar, and
management-ready reporting without waiting for the full OrdoCAD suite.

## Shipped

- Flask app factory + Alembic-managed schema (was a 2,500-line
  `app.py` with runtime schema mutation).
- Five tracker tables (Project Work, CAD Development, Training,
  Capability, Suggestion Box) plus comments + activity_log.
- Per-record comments thread.
- Activity audit log on create / edit / status change / comment /
  attachment.
- Full-text search across trackers.
- CSV export.
- AI Intake (LiteLLM) with cloud fallback.
- Retired the old external calendar glance; internal calendar is planned as a first-class TaskTrack module.
- Telegram capture bot (`@MyTrack_Tasks_Bot`).
- Unified inbox capture (`/api/v1/inbox`) — single surface for
  Telegram, paperless, voice memos, and any other Nexus app to
  drop items into TaskTrack, with optional direct-route into one
  of the five trackers.
- Scoped API tokens (triage / bot / inbox).
- Structured logging with per-request request_id.
- Rate-limited intake forms.
- **Attachments via MinIO** (2026-05-04). 50 MB cap, extension
  whitelist, sha256 dedupe, 5-minute presigned download URLs.
- Project Number field on all five trackers (optional on four,
  required + format-validated on Project Work).

## Open / next

To be decided after the post-cleanup regroup. Candidates:

- Hyperlinks panel (the other placeholder block — sibling of the
  attachments widget).
- Reminders / notifications (in-app, Telegram).
- Saved views and richer dashboard filters.
- Image / PDF preview thumbnails for attachments.
- AI autopilot polish — let triage do more of the work.
- Internal calendar module: events, meeting prep, project milestones, task due dates, report deadlines, and follow-ups.
- Project workspace MVP: project detail panel, internal notes, linked tasks, and map/report actions.
- Report engine MVP: management weekly, project status, portfolio/map summaries, and batch meeting packets.

## Explicitly out

- Immediate Postgres rewrite. SQLite stays until project geometry/reporting needs justify Postgres/PostGIS.
- Broad RBAC/productization. Add privacy/reporting tiers only where needed for operator safety.
- M365 / Outlook integrations.
- SLA timers tied to a customer-facing service desk.
- BR Engineering branding or company-VM install machinery.
- **CAD Project Setup System metadata** (jurisdictions, stakeholders,
  project area / boundary, sheet indices, CAD/Civil 3D templates,
  client/title-block presets, override / status / alert workflow,
  GIS/aerial/LiDAR background data). All of this lands in OrdoCAD,
  not TaskTrack — see
  `~/projects/ordocad/docs/SEPARATION_AND_DEPLOY_PLAN.md` (2026-05-22).
  TaskTrack stays the Flow/reporting cockpit; canonical project metadata
  and spatial work areas are owned by Atlas/OrdoCAD as those links mature.

## Not yet decided

- Whether to keep the AI Intake LiteLLM cloud fallback or restrict to
  a local model.
- Whether to rename any of the five tracker tables now that the
  firm-shop framing is no longer load-bearing.
