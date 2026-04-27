# BR Task Tracker

Internal service-desk / ticket tracker for a civil engineering firm.
Replaces a mix of email, sticky notes, and "did you do that thing?"
hallway conversations with five tracked queues:

- **Project Work** — billable execution work tied to project numbers.
- **CAD Development** — discretionary CAD changes, fixes, follow-ups.
- **Training** — coaching and training requests as planned work.
- **Capability Tracking** — long-running observations of CAD skill
  gaps (manager-restricted; HR-adjacent).
- **Suggestion Box** — ideas that may become assigned work later.

Plus per-row comments, an activity audit log, full-text search across
trackers, CSV export, and a public submission-form surface for
employees who'd rather not open the dashboard.

## Stack

Plain, conservative, easy to deploy.

- **Runtime**: Flask + gunicorn behind a Linux systemd unit.
- **DB**: SQLite (single file, served by SQLAlchemy + Alembic).
- **Frontend**: Server-rendered Jinja templates + vanilla JS in one
  big `index.html`. No build step.
- **Auth**: Local DB-backed (email + werkzeug password hash). LAN
  access control sits in front for the prototype.
- **Two deployment profiles**:
  - `personal` — Josh's local dev install on Nexus (calendar widget,
    AI Intake experiment, Telegram capture bot).
  - `company` — the BR rollout (above features off, intake forms
    require login, structured JSON logs, "BR Task Tracker" branding).

## Quick start (company VM)

See **[DEPLOY.md](DEPLOY.md)** for the full runbook. TL;DR:

```bash
git clone https://github.com/Rtoony/BR_Tasktrack.git
cd BR_Tasktrack
sudo ./scripts/install.sh
cd /opt/tasktrack
sudo -u patheal env DB_PATH=/var/lib/tasktrack/tracker.db \
    ./.venv/bin/python -m flask --app wsgi create-admin \
        --email you@brengineering.com --name "Your Name"
curl http://127.0.0.1:5050/healthz   # -> ok
```

Open `http://<vm-ip>:5050/login` from any LAN workstation, log in with
the admin email/password you just set, and you're running.

## Local development

```bash
git clone https://github.com/Rtoony/BR_Tasktrack.git
cd BR_Tasktrack
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
flask --app wsgi db-upgrade            # builds tracker.db at the project root
flask --app wsgi create-admin --email me@example.com --name "Me"
make run-dev                            # gunicorn on :5050
make test                               # pytest (13 in-process + smoke)
make smoke                              # HTTP smoke against the running service
```

## Architecture in one screen

```
app/
├── __init__.py        create_app() factory + middleware wiring
├── config.py          ALLOWED_TABLES / SIMPLE_SUBMISSION_CONFIGS / ADMIN_WORKFLOW_VIEWS
├── profile.py         TASKTRACK_PROFILE personal|company + feature flags
├── db.py              SQLAlchemy engine + per-request session
├── models.py          Declarative models for all 12 tables + to_dict
├── auth.py            login_required / admin_required decorators
├── tokens.py          Scoped API tokens (triage / personal / bot)
├── logging_config.py  text + JSON formatters with request_id field
├── middleware.py      X-Request-Id round-trip + structured access log
├── cli.py             flask db-upgrade / init-db / create-admin
├── services/
│   ├── audit.py       log_activity (writes to activity_log)
│   ├── tickets.py     validate / extra fields / create_direct_record
│   ├── triage.py      AI Intake (LiteLLM) — disabled in company profile
│   └── calendar.py    Radicale .ics reader — disabled in company profile
└── routes/
    ├── auth.py        /login /register /logout
    ├── main.py        / /healthz
    ├── intake.py      /intake/* (renamed from /submit/*)
    ├── api.py         /api/v1/{dashboard,search,<table>/...}
    ├── admin.py       /admin /api/v1/admin/*
    ├── triage.py      /api/v1/triage (feature-flagged)
    ├── maximus.py     /api/v1/maximus/* (feature-flagged)
    ├── calendar.py    /api/v1/calendar/upcoming (feature-flagged)
    └── telegram_api.py /api/v1/telegram/{pair,touch,tickets}

deploy/
├── tasktrack.env.template      → /etc/tasktrack/tasktrack.env
└── tasktrack.service.template  → /etc/systemd/system/tasktrack.service

migrations/                  Alembic baseline + future revisions
scripts/
├── install.sh               System install / upgrade (idempotent)
└── smoke.sh                 HTTP-level smoke against a running instance
templates/                   Jinja templates (index / admin / login / intake)
tests/                       pytest — HTTP smoke + in-process Flask test client
wsgi.py                      Gunicorn entrypoint
gunicorn.conf.py             Workers / timeout / log routing
alembic.ini                  Alembic config
```

## Long-form planning history

`SERVICE_DESK_RESTRUCTURE.md` is the canonical record of phased
implementation decisions: deployment profiles, RBAC vocabulary,
hierarchical teams, the AI-set-aside decision, etc. Read it if you
care about the *why* behind the structure.

## Status

**Phase 1 complete (2026-04-27).** The codebase has been carved from a
single 2,500-line `app.py` into a proper Flask package, moved from
ad-hoc runtime schema mutation to Alembic-managed migrations, and
hardened with structured logs / request IDs / rate limits / scoped
API tokens. Phase 2 (Postgres) and Phase 3 (RBAC) are the next major
deliverables.

## License

(unset — internal firm tool)
