# TaskTrack — Roadmap

Single-user personal task tracker on Nexus. No company rollout, no
multi-tenant, no RBAC. The five trackers stay; personal-life trackers
may be added alongside them later.

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
- Calendar widget reading Radicale `.ics` files.
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
- Personal-life tracker(s) added alongside the five firm-shaped ones.

## Explicitly out

- Postgres migration (SQLite is fine for one user).
- RBAC / multi-user permissions (one user).
- M365 / Outlook integrations.
- SLA timers tied to a customer-facing service desk.
- BR Engineering branding or company-VM install machinery.

## Not yet decided

- Whether to keep the AI Intake LiteLLM cloud fallback or restrict to
  a local model.
- Whether to rename any of the five tracker tables now that the
  firm-shop framing is no longer load-bearing.
