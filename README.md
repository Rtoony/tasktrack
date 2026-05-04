# TaskTrack

Personal single-user task / ticket tracker. Five queues, comments, an
activity audit log, full-text search, attachments via MinIO, a Telegram
capture bot, and a Radicale calendar widget.

The five trackers:

- **Project Work** — billable execution work tied to project numbers.
- **CAD Development** — discretionary CAD changes, fixes, follow-ups.
- **Training** — coaching and training-related work items.
- **Capability Tracking** — long-running observations of CAD skill
  gaps.
- **Suggestion Box** — ideas that may become assigned work later.

## Stack

Plain, conservative, easy to keep running.

- **Runtime**: Flask + gunicorn, run as a systemd user unit on Nexus.
- **DB**: SQLite (single file, SQLAlchemy + Alembic).
- **Frontend**: Server-rendered Jinja + vanilla JS in one big
  `index.html`. No build step.
- **Auth**: Local DB-backed (email + werkzeug password hash).
- **Attachments**: MinIO (docker-compose under `deploy/`),
  127.0.0.1:9000 API + :9001 console. 50 MB cap. Whitelist:
  PDF, DWG, DXF, PNG, JPG, XLSX, DOCX.

## Local development

```bash
git clone <repo>
cd collab-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Schema
alembic upgrade head            # builds/updates tracker.db at the project root
flask --app wsgi create-admin --email me@example.com --name "Me"

# MinIO (one-time setup)
cp deploy/minio.env.example deploy/minio.env
# edit deploy/minio.env to set MINIO_ROOT_PASSWORD + MINIO_SECRET_KEY (same value)
./deploy/setup-minio.sh

make run-dev                    # gunicorn on :5050
make test                       # in-process pytest
make smoke                      # HTTP smoke against the running service
```

## Architecture in one screen

```
app/
├── __init__.py        create_app() factory + middleware wiring
├── config.py          ALLOWED_TABLES / SIMPLE_SUBMISSION_CONFIGS / ADMIN_WORKFLOW_VIEWS
├── profile.py         Single-user settings (env-overridable)
├── db.py              SQLAlchemy engine + per-request session
├── models.py          Declarative models for all 13 tables + to_dict
├── auth.py            login_required / admin_required decorators
├── tokens.py          Scoped API tokens (triage / personal / bot)
├── logging_config.py  text + JSON formatters with request_id field
├── middleware.py      X-Request-Id round-trip + structured access log
├── cli.py             flask db-upgrade / init-db / create-admin
├── services/
│   ├── audit.py       log_activity (writes to activity_log)
│   ├── tickets.py     validate / extra fields / create_direct_record
│   ├── triage.py      AI Intake (LiteLLM)
│   ├── calendar.py    Radicale .ics reader
│   └── attachments.py MinIO upload / list / delete / presigned URL
└── routes/
    ├── auth.py        /login /register /logout
    ├── main.py        / /healthz
    ├── intake.py      /intake/*
    ├── api.py         /api/v1/{dashboard,search,<table>/...}
    ├── admin.py       /admin /api/v1/admin/*
    ├── triage.py      /api/v1/triage
    ├── maximus.py     /api/v1/maximus/*
    ├── calendar.py    /api/v1/calendar/upcoming
    ├── telegram_api.py /api/v1/telegram/{pair,touch,tickets}
    └── attachments.py /api/v1/attachments/...

deploy/
├── docker-compose.minio.yml    MinIO container
├── minio.env.example           credential template
├── setup-minio.sh              one-shot bring-up + bucket bootstrap
├── tasktrack.env.template      env file template for the systemd unit
└── tasktrack.service.template  systemd unit template

migrations/                  Alembic revisions
scripts/
└── smoke.sh                 HTTP-level smoke against a running instance
templates/                   Jinja templates (index / admin / login / intake)
tests/                       pytest — HTTP smoke + in-process Flask test client
wsgi.py                      Gunicorn entrypoint
gunicorn.conf.py             Workers / timeout / log routing
alembic.ini                  Alembic config
```

## Roadmap

See `ROADMAP.md` for what's shipped, what's open, and what's idea-only.
