# TaskTrack — Service Desk Restructure Plan (v4)

> **Captured**: 2026-04-26
> **Supersedes**: v3 of this document.
> **Direction**: Decisively professional internal service desk for one civil engineering firm. Productization optional, secondary. Personal-task functionality is removed from the company product.
> **Status**: Discovery and planning complete. No code changes yet. Phase 0 (stabilize) blocks everything below.

---

## What Changed in v4

1. **AI data policy moved from Phase 7 → Phase 1B.** Cloud fallback default-off, per-call audit row, raw-input retention setting all land before professional use. Full retention machinery (purge cron, hash-only conversion) stays in later phase.
2. **Telegram identity mapping moved from Phase 4 → Phase 1C.** Pairing requires authenticated TaskTrack user; `telegram_chat_access.user_id` FK added during 1C. Bot rejects ticket creation from unbound chats from day one.
3. **Email intake idempotency reframed as conditional.** Timer stays disabled by default; if/when revived, idempotency must ship first.
4. **Phase 1D split into 1D-1 and 1D-2.** 1D-1 = models + Alembic baseline + app unchanged. 1D-2 = blueprint-by-blueprint conversion. No "rewrite all routes in one pass."
5. **Added `TASKTRACK_PROFILE=company|personal` deployment profile.** Company profile = calendar off, cloud AI off, intake forms restricted, localhost bind, firm branding. Decided in Phase 1B. Same code, profile is the switch.
6. **Jinja partial extraction permitted earlier** when it reduces risk during RBAC UI work. Rule is "no framework / no redesign," not "never touch the template."
7. **Permission vocabulary added before RBAC coding.** Permissions are atomic verbs (`ticket.read`, `ticket.assign`, `capability.write`, `admin.users`); roles are bundles of permissions. Documented now, used by Phase 3 implementation.
8. **Watchers schema added to Phase 3.** `ticket_watchers (ticket_table, ticket_id, user_id, added_by_user_id, added_at)`. Watcher actions also have permissions.
9. **"Public submission forms" renamed to "internal request intake forms"** in planning. Decision required before company rollout: anonymous-with-link-token, login-required, or both. Anonymous capability submissions specifically forbidden regardless of profile.
10. **Phase 7 reworded "remove or spin out `personal_tasks`."** Default expectation: delete after Maximus confirms it isn't depended on. Spin out only if Maximus needs it.

---

## Decisions Locked In (2026-04-26 follow-up)

Five open decisions resolved by owner; cascading changes applied throughout this document.

| # | Decision | Resolution |
|---|---|---|
| 4 | Intake-form auth in company profile | **Login required.** No anonymous submissions, no link-token alternative. Personal profile may keep anonymous for Josh's local use. |
| 5 | Product naming | **"BR Task Tracker"** (display name) / **`BR_TaskTrack`** (slug/identifier). Matches existing `@BR_Task_Admin_Bot` naming pattern. Public ticket ID prefix becomes `BR-2026-####`. |
| 11 | Calendar widget | **Off in company profile** as default. Phase 8 will add an **Outlook (Microsoft Graph API) integration** to replace the Radicale-direct widget for the company product. |
| 13 | Profile defaults | Approved as drafted. The values in the Deployment Profile Concept table below are now the locked baseline. |
| 14 | Permission vocabulary | Approved as drafted. The atomic verbs and default role bundles in the Permission Vocabulary section below are now the locked baseline for Phase 3 implementation. |

**Cascading implications already applied below**:
- Profile table: `INTAKE_FORM_AUTH` company value locked to `required`. `BRAND_NAME` company value locked to `"BR Task Tracker"`.
- Phase 1C intake-form rename now also enforces login on `/intake/*` in company profile.
- Phase 3 triage-queue work is simpler: every company-profile intake submission has a `requester_user_id` (no unowned-row backlog).
- Phase 5 public ticket ID pattern: `BR-2026-####` (was `CIV-2026-####`).
- Phase 8 service-desk feature list: added "Outlook (Microsoft Graph) calendar integration" as a tracked deliverable.
- Open Decisions section trimmed: items 4, 5, 11, 13, 14 removed (and #10 partially resolved-by-implication since login-required intake means no anonymous rows to triage in company profile).

The repository / systemd service rename to `br-task-tracker` is **deferred** — risky during the foundation phases. Revisit after Phase 2 (Postgres) ships cleanly.

---

## Decisions Locked In (2026-04-26 batch 2)

Most remaining open decisions resolved by owner; cascading changes applied throughout this document.

| # | Decision | Resolution | Cascading impact |
|---|---|---|---|
| 1 | Hosting | **Firm-owned infra eventually.** Nexus is transitional. | Justifies aggressive config externalization (Phase 1B). Phase 8+ needs a "deploy to firm server" packaging story. |
| 2 | Capability subject visibility | Build with **safe default: subject cannot see own capability notes.** Make it admin-configurable so HR can relax it later. | Phase 4 ships with `ALLOW_CAPABILITY_SELF_VIEW=false` default; admin UI exposes the toggle. |
| 3 | Team structure | **Hierarchical — teams within teams.** | Phase 3 `teams` table gets `parent_team_id` self-FK. Manager-of-team scope expands to descendant teams via recursive CTE. |
| 6 | Auth source of truth | **Self-hosted DB-backed authentication.** Possible future read-only sync from firm's project DB and employee DB. | No M365 / Google SSO planned. Phase 1A app factory and Phase 3 user model designed to allow integration adapters later. |
| 7 | Compliance / retention | **TBD by firm.** | Plan ships with sensible defaults (90/365 day AI retention; no auto-delete on tickets). Design retention values as admin-configurable per category. |
| 9 | Role hierarchy | **Admin > Principal > Manager > Support** (4 tiers). The lowest tier renamed from "employee" → **"support"** throughout the plan. | All references to `employee` role updated to `support`. Permission bundles, migration paths, and seed data updated. |
| 10 | Intake routing | **Triage queue.** Unassigned intake submissions land in a queue for the designated triage owner to route. | Phase 3 adds: a triage state on tickets (`triage_state = new \| triaged \| assigned`), a `/triage` UI for managers/admins, and an `intake_default_assignee_user_id` setting that can auto-route specific source/category combinations. |
| 12 | AI raw-input retention values | **90 days company / 365 days personal**, confirmed. | No change. |
| 15 | Watcher = notification trigger | **Yes, but defer wiring to Phase 8.** Schema lands in Phase 3 as planned. | Phase 3 schema unchanged; Phase 8 notifications layer wires `ticket_watchers` rows to email/Telegram/in-app delivery. |
| 17 | Repo / service rename | **Deferred** (per-plan). | No change. Revisit after Phase 2. |

Items still open after batch 2:
- **#8** `personal_tasks` REMOVE vs spin out — needs Maximus operator confirmation before Phase 7.
- **#16** Gunicorn worker count — revisit after Phase 1B structured logging shows queueing or not.

---

## Decisions Locked In (2026-04-27 — AI Intake set aside)

AI Intake is **experimental and not in the initial company release.** Treated the same way as the calendar widget: feature-flagged off in the company profile, on in the personal profile. Code stays in the repo (dormant, not deleted) so Josh can keep experimenting locally and so re-enabling later is a one-flag flip.

**Cascading impact**:
- New profile flag `ENABLE_AI_INTAKE` (company default `false`, personal default `true`) joins the deployment-profile table.
- `app/routes/triage.py` blueprint registers conditionally in `create_app()`. When disabled, `/api/triage` and `/api/<table>/<id>/confirm` simply do not exist (404, not 503).
- `templates/index.html` gates the "AI Intake" tab button, the entire `#sec-intake` section, and the `initIntake()` JS call behind a Jinja conditional. Tab disappears in company profile.
- The AI-specific Phase 1B work is **dropped**: no `AI_ALLOW_CLOUD_FALLBACK` flag, no `AI_RAW_INPUT_RETENTION_DAYS` setting, no `ai_triage_calls` audit table. Phase 1B becomes pure config + middleware work, **no schema changes at all** (the "one schema mutation outside Alembic" exception is removed).
- Phase 1C drops AI-specific work; the `TASKTRACK_TOKEN_TRIAGE` scoped token still gets split out for personal-profile use but is unused in company.
- Phase 6 (originally email-idempotency + AI retention) is **reduced and effectively deferred**. Email intake fed AI Intake; with AI off there's nothing for it to feed. If email intake is ever revived for a non-AI purpose (forwarding into the triage queue for manual routing), the `email_intake_processed` idempotency table lands then.
- Phase 3 permission vocabulary: `ai.triage` and `ai.config.write` are demoted to **experimental permissions** — granted only when `ENABLE_AI_INTAKE=true`. No change to the atomic-verb design.
- Phase 8 service-desk feature list is unaffected (attachments, SLA, threading, notifications, Outlook, KB).

**Removal vs deactivation**: chose deactivation (feature flag). Reasoning:
- Code is small and isolated (one route blueprint + one service module).
- Telegram bot's `/api/triage` call still works in personal profile.
- Re-enabling later is a one-line config change, not a port-the-feature-back exercise.
- Deleting outright would also need to strip `templates/index.html` AI Intake markup permanently, which is more disruptive.

If the firm later confirms AI is permanently out, Phase 7 (or a new dedicated phase) can do the cleanup: delete `app/routes/triage.py`, `app/services/triage.py`, the AI Intake template section, the LiteLLM env vars, and the experimental permissions.

---

## Executive Recommendation

Make TaskTrack a real internal service desk for one civil engineering firm. Stop treating it as a personal pet project AND don't pretend it's a SaaS product yet.

The right sequencing is:
1. **Stabilize** the dirty git state (Phase 0).
2. **Lift the foundation** — deps + tests + app factory (1A), then config + health + AI policy + profile (1B), then API v1 + scoped tokens + Telegram identity + bot decoupling (1C).
3. **Introduce the data abstraction** — models + Alembic baseline (1D-1), then convert blueprints (1D-2). Only then move to Postgres (Phase 2).
4. **Add real users, teams, RBAC, watchers** with a permission-based model (Phase 3).
5. **Carve out Capability Tracking** as a separate code path (Phase 4).
6. **Public ticket IDs + structured audit log** (Phase 5).
7. **Email intake idempotency + AI retention** (Phase 6, conditional on reviving intake).
8. **Remove or spin out `personal_tasks`** (Phase 7).
9. **Service-desk features** (Phase 8). Re-evaluate table unification (Phase 9, optional).

Tenant-readiness is cheap if done now (`organization_id DEFAULT 1` slot, env-driven branding). Real multi-tenant work waits until there's a customer.

---

## Recommended Product Boundary

**TaskTrack-the-product = professional internal service desk for one civil firm.**

In scope (the company product):
- Project Work, CAD Development, Training, Suggestion Box
- Capability Tracking (separate restricted module — Phase 4)
- Comments, activity log, search, CSV export
- AI Intake (local-only by default in company profile)
- Internal request intake forms (renamed; auth/rate-limit per profile)
- Public ticket IDs (`BR-2026-####`-style)
- Future: attachments, SLA timers, notifications, email threading

Out of scope:
- Personal tasks → remove or spin out (Phase 7)
- Multi-tenant SaaS features → defer; reserve `organization_id` slot only
- Customer-facing public ticketing portals → revisit if firm needs to give clients a request URL
- Calendar widget → off in company profile (Nexus-specific Radicale dependency)

---

## Deployment Profile Concept

`TASKTRACK_PROFILE` env var. Company-rollout build defaults to `company`. Josh's local internal build runs `personal`.

| Setting | personal | company |
|---|---|---|
| `ENABLE_AI_INTAKE` | true | **false** (experimental, set aside per 2026-04-27 decision) |
| `ENABLE_CALENDAR_WIDGET` | true | false |
| `INTAKE_FORM_AUTH` | none | **required** (login required; no anonymous, no link-token) |
| `INTAKE_FORM_RATE_LIMIT_PER_HR_PER_IP` | 60 | 10 |
| `BIND_HOST` | 0.0.0.0 | 127.0.0.1 (cloudflared proxies) |
| `BRAND_NAME` | "TaskTrack" | "BR Task Tracker" |
| `LOG_FORMAT` | text | structured (JSON/logfmt) |
| `ENABLE_DEBUG_ROUTES` | true | false |
| `ALLOW_HARD_DELETE` | true | false (soft-delete only) |

Profile sets defaults; individual env vars override. Overriding a company-profile setting in production logs a startup warning ("AI cloud fallback explicitly enabled in company profile by env override").

Decided as part of Phase 1B externalization.

---

## Permission Vocabulary

Atomic verbs. Roles bundle them. New features add new permissions, not new role checks. Used by Phase 3 RBAC implementation.

### Tickets (work_tasks, project_work_tasks, training_tasks)
- `ticket.list` — list/search (always scoped through `visible_tickets`)
- `ticket.read` — fetch single (scoped)
- `ticket.create`
- `ticket.update` — change fields incl. status (object-level scope)
- `ticket.assign` — set assignee
- `ticket.delete` — soft-delete (object-level scope)
- `ticket.comment` — add comment (object-level scope)
- `ticket.export` — CSV (scoped, audited)
- `ticket.confirm_ai` — clear `needs_review` flag

### Suggestions
- `suggestion.list`, `suggestion.read`, `suggestion.create`
- `suggestion.review` — change status during review
- `suggestion.promote` — promote-to-CAD

### Capability (separate code path — never composes with ticket scope)
- `capability.list`, `capability.read`
- `capability.create`, `capability.update`
- `capability.delete` (admin only)
- `capability.export` (admin only, audited)

### Watchers
- `watcher.add_self`
- `watcher.add_other` (manager+ team scope)
- `watcher.remove_self`
- `watcher.remove_other` (manager+ team scope)
- `watcher.list`

### Admin / System
- `admin.users.read`, `admin.users.write`
- `admin.teams.read`, `admin.teams.write`
- `admin.allowlist.write`
- `admin.tokens.write`
- `admin.telegram.pair`, `admin.telegram.unpair`
- `admin.config.write`
- `admin.audit_log.read`

### AI
- `ai.triage` — invoke triage endpoint
- `ai.config.write` — toggle cloud fallback (admin)

### Default role bundles

**support** (lowest tier — workforce: CAD techs, drafters, junior engineers handling the bulk of the work):
- `ticket.{list,read,create,update,comment,export,confirm_ai}` — all scoped to own
- `suggestion.{list,read,create}`
- `watcher.{add_self,remove_self,list}`
- `ai.triage`

**manager** = support + 
- `ticket.{assign,delete}` (team scope, **including descendant teams** via the hierarchical `parent_team_id` chain)
- `capability.{list,read,create,update}` (team scope, including descendants)
- `suggestion.{review,promote}`
- `watcher.{add_other,remove_other}` (team scope)

**principal** = manager + 
- `ticket.*` (firm-wide scope, except capability)
- `capability.*` only with explicit `capability_grant` row
- `admin.audit_log.read`

**admin** (highest tier) = everything

Implementation: `users.role` stays a single column for now; a `role_permissions(role, permission)` table seeds default bundles; check is `current_user.has_permission('ticket.assign')` not `current_user.role == 'manager'`. Adding a custom role later is a config change, not a code change.

---

## RBAC / Visibility Model

**Scoped queries**, not `visible_ticket_ids_for(user)`. Authorization is a query transformation:

```python
def visible_tickets(session, user, model):
    if not user.has_permission('ticket.list'):
        return session.query(model).filter(False)
    if user.has_permission('ticket.scope.all'):
        return session.query(model)
    if user.has_permission('ticket.scope.team'):
        team_ids = team_member_ids(session, user.team_id)
        return session.query(model).filter(or_(
            model.assignee_user_id.in_(team_ids),
            model.requester_user_id.in_(team_ids),
            model.created_by_user_id == user.id))
    return session.query(model).filter(or_(
        model.assignee_user_id == user.id,
        model.requester_user_id == user.id,
        model.created_by_user_id == user.id))

def capability_visible(session, user):  # NEVER composes with visible_tickets
    if user.has_permission('capability.scope.all'):
        return session.query(PersonnelIssue)
    if user.has_permission('capability.scope.team'):
        team_ids = team_member_ids(session, user.team_id)
        return session.query(PersonnelIssue).filter(
            PersonnelIssue.person_user_id.in_(team_ids))
    return session.query(PersonnelIssue).filter(False)
```

Object-level fetches: `visible_tickets(...).filter(model.id == record_id).one_or_none()` → 404 (not 403, avoids leaking existence). Watchers extend visibility: a `ticket_watchers` membership grants `ticket.read` on that one ticket only.

Runtime guard in dev/test: a SQLAlchemy event hook flags raw queries that touched a ticket model without going through a scope helper.

---

## Tracker-by-Tracker Disposition

| Tracker | Decision | Notes |
|---|---|---|
| Project Work | Keep — central tracker. Add `assignee_user_id`, `requester_user_id`. | Service-desk spine |
| CAD Development | Keep — central tracker. Same FK upgrades. | |
| Training | Keep. Same FK upgrades. | |
| Suggestion Box | Keep. Lower-permission visibility. | Promotion-to-CAD already works |
| Capability Tracking | **Keep but isolate as separate module.** Own blueprint, own templates, own scope helper, optional own Postgres schema. | HR-adjacent; highest-consequence permission risk |
| `personal_tasks` | **Remove or spin out** (Phase 7). Default = remove. | 0 rows; Maximus likely doesn't need |
| Calendar widget | **Off in company profile.** Available in personal profile. | Nexus-specific Radicale dependency |
| AI Intake | Keep. Cloud fallback off in company profile. | High-value differentiator |
| Internal request intake forms (was "public submission forms") | Renamed. Auth/rate-limit per profile. **Capability submissions never anonymous regardless of profile.** | renamed; capability route removed from intake |

---

## Corrected Roadmap

| # | Phase | Schema change? | Data store change? |
|---|---|---|---|
| 0 | Stabilize | no | no |
| 1A | Project foundation (deps, pytest, app factory, no import-time `init_db`) | no | no |
| 1B | Config + health + service hardening + **profile concept** + **AI data policy controls** | minimal (one audit table) | no |
| 1C | API v1 + scoped tokens + Telegram decoupling + **Telegram identity mapping** + **rename intake forms** | minimal (`telegram_chat_access.user_id`) | no |
| 1D-1 | Models + Alembic baseline; app still uses raw SQL | no (logical: models match physical) | no |
| 1D-2 | Convert blueprints to SQLAlchemy one at a time | no | no |
| 2 | Postgres migration | no | **yes** |
| 3 | Permission model + users/teams + RBAC scoped queries + watchers schema | yes | no |
| 4 | Capability Tracking carved out (own blueprint, scope, optional own Postgres schema) | yes | no |
| 5 | Public ticket IDs + structured audit log | yes | no |
| 6 | Email intake idempotency + AI retention machinery (**conditional on reviving intake**) | yes | no |
| 7 | **Remove or spin out** `personal_tasks` (default = remove) | yes | no |
| 8 | Service-desk features (attachments → SLA → threading → notifications → KB) | yes | no |
| 9 | Optional: re-evaluate ticket-table unification | yes | no |

Each phase is independently shippable. No phase bundles a data model change with an infrastructure change.

---

## Why Raw SQLite-to-Postgres Migration Is Risky in This Codebase

1. **`init_db()` mutates schema at import time.** Two gunicorn workers race on `ALTER TABLE`; `normalize_ticket_tables()` does SQLite-only `DROP+RENAME`. The explicit `try/except duplicate column` is a smell. None of this translates.
2. **All queries are raw f-string SQL** (~50 touchpoints). SQLite-isms vs Postgres-isms hit individually: `COLLATE NOCASE`, `INTEGER PRIMARY KEY AUTOINCREMENT`, integer-as-bool, JSON-as-string, `CURRENT_TIMESTAMP` semantics, `COALESCE`/`NULLIF` chains.
3. **No abstraction means every endpoint must be fixed individually** if Postgres returns a different type or NULL handling.
4. **`g.db = sqlite3.connect(DB_PATH)` is hardcoded** in `get_db()`. Same in `telegram_bot.py`'s `db_connect()` and `email_intake.py`.
5. **Telegram bot writes the SQLite file directly.** A Postgres switch breaks it silently.
6. **No tests means a botched migration silently breaks features** until a user hits one.
7. **The DB is tiny right now (3 work_tasks)** — that's an *opportunity*. Doing the SQLAlchemy abstraction now, while data is small, is dramatically easier than later.

The fix is **Phase 1D-1 first** (models + alembic baseline, no route changes), then **Phase 1D-2** (blueprint conversion), then **Phase 2** (engine swap). Phase 2 then becomes "swap connection string + alembic on Postgres + dump/load," not "rewrite ~50 endpoints while changing engines."

---

## Phase 0 — Stabilize

**Goal**: stabilize current state so Phase 1A can begin from a clean tested baseline. Zero behavior changes.

**Tasks**:
1. **Snapshot DB**: `cp ~/projects/collab-tracker/tracker.db ~/projects/collab-tracker/tracker.db.phase0-$(date +%Y%m%d).bak`
2. **Resolve dirty diff**: `git status` / `git diff --stat`. For each modified file, show diff to operator, decide commit-with-message or revert. Don't bundle unrelated changes.
3. **Drop orphan**: `sqlite3 tracker.db "DROP TABLE IF EXISTS project_work_tasks__new;"` then verify with `.tables`
4. **Verify backups capture `tracker.db`**: check `~/scripts/nexus-backup.py`; spot-check NAS
5. **Add `scripts/smoke.sh`** (curl `/healthz`, `/login`, exit non-zero on failure)
6. **Run smoke green**
7. **Commit Phase 0 artifacts** (smoke script + DB cleanup if scripted) with single clear message

**Exit**: clean working tree, snapshot exists, smoke passes, orphan table gone.

**Rollback**: restore snapshot, `git revert` Phase 0 commit. App unchanged.

---

## Phase 1A — Project Foundation

**Goal**: dep pinning + test framework + app factory pattern. `init_db()` no longer runs at import. **Zero behavior change.**

**Tasks**:
1. **Pin dependencies**: `requirements.txt` (flask, gunicorn, werkzeug, requests, icalendar) + `requirements-dev.txt` (pytest, pytest-flask)
2. **App factory**: create `app/__init__.py` with `create_app(config=None) -> Flask`. Move route handlers into per-area blueprints (`main`, `api`, `admin`, `submit`, `triage`, `maximus`, `calendar`). Move shared helpers into `app/db.py` and `app/services/*.py`.
3. **CLI for init_db**: `flask init-db` runs schema bootstrap. Live DB unchanged.
4. **WSGI entry**: `wsgi.py` calls `create_app()`. Update `collab-tracker.service` ExecStart from `app:app` to `wsgi:app`.
5. **Pytest scaffolding**: `tests/conftest.py` with temp-DB fixture; `tests/test_smoke.py` covering `/healthz`, login, one CRUD round-trip, `/admin` denial for non-admin.
6. **Makefile**: `make test`, `make run-dev`, `make smoke`.
7. **Reload systemd**, run smoke + pytest, confirm app behaves identically.

**Exit**: pytest green, app still serves, `init_db` no longer runs at import, requirements committed.

**Non-goals**: no Postgres, no SQLAlchemy, no schema changes, no Telegram changes, no API rename.

**Rollback**: revert systemd unit edit, revert package layout commit. SQLite untouched.

---

## Phase 1B — Config, Health, Service Hardening, Profile, AI Policy Controls

**Goal**: stop having configuration baked into Python literals. `/healthz` actually meaningful. Tighten service surface. Establish AI data policy and deployment profile concept. **End-user behavior unchanged.**

**Tasks**:
1. **Externalize config (zero-disk policy)**: move every literal value to env (BASE_URL, BRAND_NAME, LITELLM_BASE_URL, LITELLM_API_KEY, RADICALE_COLLECTIONS_ROOT, TRIAGE_MODEL_LOCAL, TRIAGE_MODEL_CLOUD, TRIAGE_TIMEOUT_S). Vault item `Nexus - TaskTrack`. Systemd unit gets `ExecStartPre=nexus-svc-inject collab-tracker "TaskTrack"` + `EnvironmentFile=-/dev/shm/nexus-env-collab-tracker` + `ExecStopPost=rm -f ...`. **No `.env` file.**
2. **Implement `TASKTRACK_PROFILE`**: company/personal switch with the defaults table above. Individual env vars override; overriding company-profile defaults logs a startup warning.
3. **AI data policy controls** (the policy ships now; full retention machinery is Phase 6):
   - `AI_ALLOW_CLOUD_FALLBACK` env (default false in company profile, true in personal). Triage code respects this — when false, fail with a clear error instead of falling back to cloud.
   - `AI_RAW_INPUT_RETENTION_DAYS` env (default 90 / 365 per profile). Just a setting; cron purge is Phase 6.
   - **`ai_triage_calls` table** capturing: actor (user_id or token name), source (paste/email/telegram/maximus), target_table, model_used, cloud_fallback_used (bool), raw_input_chars, raw_input_sha256, result_task_id, created_at. This is the ONE schema addition outside Alembic; do it now via the existing `ensure_column`/migration path, then commit to no further schema changes until 1D-1.
   - Triage endpoint writes one row per call, regardless of success/failure.
4. **Real `/healthz`**: 200 with `{"status":"ok","db":"ok","git_sha":"...","profile":"company"}` after a 1-row DB ping. 503 if DB ping fails.
5. **Structured logging**: replace plain access log with structured (JSON or logfmt). Request-ID middleware emits `X-Request-Id` and includes it in every log line. Don't log request bodies.
6. **Error handlers**: 4xx/5xx return request ID, not stack trace. Log traceback server-side with request ID.
7. **Gunicorn config**: `gunicorn.conf.py` checked in. `graceful_timeout=30`, `timeout=120` (until triage is async), workers=2.
8. **CSRF on session-auth APIs**: Flask-WTF or seasurf. Token tokens (`X-Token`) bypass correctly. Document the pattern.
9. **Rate limit framework**: `flask-limiter` registered. Apply per-IP per-form caps to `/intake/*` (renamed in 1C) — limits come from profile.
10. **Bind host from profile**: company profile binds `127.0.0.1`; cloudflared proxies. Personal stays `0.0.0.0`.

**Exit**:
- No literal credentials/hostnames/URLs in Python source
- `nexus-env-guard` reports clean
- `/healthz` reflects DB state and profile
- All log lines have request ID
- Profile switch verified: company turns calendar off, AI cloud off, intake forms rate-limited
- AI triage calls logged with model + cloud_used + raw_input_chars + raw_input_sha256
- CSRF + rate limit middleware active

**Non-goals**: no API rename, no token splitting (1C), no SQLAlchemy.

**Important**: this is the LAST schema mutation done outside Alembic. After 1D-1, every change goes through Alembic.

**Rollback**: revert systemd unit, revert config module. SQLite untouched (the new `ai_triage_calls` table can stay; it's empty if reverted).

---

## Phase 1C — API v1 + Scoped Tokens + Telegram Decoupling + Telegram Identity Mapping + Intake Form Rename

**Goal**: stable versioned API; ambient shared secret broken into scoped tokens; Telegram bot becomes a regular HTTP client AND every linked chat is bound to a specific TaskTrack user; "submission forms" renamed to "intake forms" with capability submissions removed.

**Tasks**:
1. **Mount routes under `/api/v1/...`** with redirects from old `/api/...` for one release cycle. Internal callers (SPA, email_intake.py) updated to v1 directly.
2. **Split `TASKTRACK_TOKEN`** into `_TRIAGE`, `_PERSONAL`, `_BOT`. Per-route scope check. Legacy single token accepted for one release with deprecation log.
3. **Telegram identity mapping** (FK addition):
   - Add `telegram_chat_access.user_id` column (NULL initially, NOT NULL after migration completes)
   - Pairing flow rewrite: a TaskTrack user, while logged in, generates **their own** pairing code; sending `/link CODE` from Telegram binds chat_id → that user_id
   - Admin can also assign a chat to a user manually (covers cases where the user can't reach the web app)
   - **Bot REJECTS ticket creation from unbound chat** with message "this chat isn't linked to a TaskTrack user — go to TaskTrack and pair it"
   - Existing rows: backfill to admin user, log warning, require re-pairing on next interaction
4. **Rewrite `telegram_bot.py` as REST client**:
   - Remove `from app import ALLOWED_TABLES, DB_PATH, validate_record_data`
   - Remove `db_connect()` and all direct `sqlite3` calls
   - Replace with `requests.post(f"{TASKTRACK_API}/api/v1/...", json=payload, headers={"X-Token": TASKTRACK_TOKEN_BOT, "X-Telegram-Chat-Id": str(chat_id)})`
   - Pairing checks move to `/api/v1/telegram/pair` endpoint; bot has zero DB access
5. **Rename intake routes** `/submit/*` → `/intake/*` with redirects from old paths. UI labels updated "submission form" → "request intake form". **Company profile enforces login on all `/intake/*` routes** (per Decision #4); personal profile may keep anonymous access for Josh's local use.
6. **Capability intake REMOVED** from `/intake/*` regardless of profile. Capability observations only via authenticated UI (Phase 4 will gate them further).
7. **Object-level token-scope tests**: requests with `_TRIAGE` cannot hit Maximus endpoints, etc.
8. **Smoke test all four integration paths**: SPA, email intake, Telegram bot, Maximus.

**Exit**:
- Routes under `/api/v1/...`
- Three scoped tokens in production; legacy logs deprecation
- Telegram bot: zero `import app`, zero `sqlite3`
- Every `telegram_chat_access` row has `user_id`; bot rejects unbound-chat tickets
- Intake routes renamed; capability intake removed
- Token-scope tests in CI

**Non-goals**: no schema beyond the `user_id` FK addition, no SQLAlchemy.

**Rollback**: revert blueprint route prefix, restore single-token auth helper. Keep the `user_id` column in DB (already nullable).

---

## Phase 1D-1 — Models + Alembic Baseline (App Still Uses Raw SQL)

**Goal**: introduce ORM and migrations layer **without changing the access layer**. App keeps running on raw `sqlite3` calls. Models exist but aren't called yet. This decoupling is the safety mechanism.

**Tasks**:
1. **Add deps**: `sqlalchemy`, `flask-sqlalchemy`, `alembic` to `requirements.txt`.
2. **Define declarative models** matching every existing table EXACTLY (including the new `ai_triage_calls` from 1B and `telegram_chat_access.user_id` from 1C):
   - `app/models/user.py` (User, ApprovedEmail)
   - `app/models/ticket.py` (WorkTask, ProjectWorkTask, TrainingTask) — separate models, no unification
   - `app/models/personnel.py` (PersonnelIssue) — separate file because Phase 4 moves it
   - `app/models/suggestion.py` (Suggestion)
   - `app/models/personal.py` (PersonalTask) — destined for Phase 7 removal/spin-out
   - `app/models/audit.py` (Comment, ActivityLog, AppSetting, TelegramChatAccess, AiTriageCall)
3. **Initialize Alembic**: `alembic init migrations/`. Configure to introspect SQLite.
4. **Create baseline migration** matching current schema exactly. **`alembic stamp head` on the live SQLite DB** so it's marked as already-at-baseline.
5. **Startup self-check**: introspect live schema at app boot; compare to model definitions; **refuse to start if drift detected**. This catches regressions during 1D-2.
6. **App keeps running with raw `sqlite3`** — no route conversions yet.
7. **Tests**:
   - Model definitions can be loaded
   - Baseline migration on a fresh DB produces the same schema as current
   - Startup self-check passes against live DB
   - `flask db current` shows baseline as head

**Exit**:
- Models defined, importable, match physical schema
- Alembic baseline = current schema
- Live DB stamped at baseline
- Startup self-check passes
- Zero behavior change

**Non-goals**: no route conversions yet, no Postgres.

**Rollback**: revert models + alembic init. Live DB is untouched (alembic stamp is just a metadata table; can be left in place).

---

## Phase 1D-2 — Convert Blueprints to SQLAlchemy, One at a Time

**Goal**: route-by-route conversion from raw `sqlite3` to SQLAlchemy session. Smoke + tests after each. Strict ordering by blast radius.

**Recommended order** (smallest blast radius first):
1. `calendar` blueprint (no DB writes)
2. `healthz` (trivial)
3. `admin` (well-bounded admin routes)
4. `intake` (renamed in 1C)
5. `triage` (depends on `activity_log` + `ai_triage_calls` + ticket inserts)
6. `api` (the bulk; further sub-divide per-table — work_tasks first, then project_work_tasks, etc.)
7. `maximus` (last because it's a separate API surface and Phase 7 may delete it)

**Per-blueprint pattern**:
- Replace `db.execute(f"SELECT ...")` with `session.query(Model).filter(...)`
- Replace `db.execute(f"INSERT ...")` with `session.add(model_instance); session.commit()`
- Move shared helpers to `app/services/*.py`
- Smoke + pytest after each
- **Never bulk-convert**; each blueprint is its own commit

**When the LAST raw `sqlite3.execute` is gone**:
- Delete `init_db()`, `ensure_column()`, `normalize_ticket_tables()`
- CLI command becomes `flask db upgrade`
- Remove the startup self-check (alembic now owns schema)

**Exit**:
- All routes use SQLAlchemy session
- No raw `sqlite3.Connection.execute` anywhere in `app/`
- `flask db upgrade` is the only schema modification path
- Tests green; live app serves on SQLite (still); no behavior change

**Rollback**: each blueprint conversion is its own commit; revert as needed. DB file is unchanged.

---

## Phase 2 — Postgres Migration

Now safe because the access layer is abstract.

**Tasks**:
- Provision Postgres
- Run alembic baseline on Postgres
- Dump existing SQLite data, transform types where needed (booleans, JSON), load into Postgres
- Switch `SQLALCHEMY_DATABASE_URI` env var
- Restart, verify
- Take a planned outage window; don't dual-write

**Exit**: app on Postgres, all tests green, smoke green, backups capture both old SQLite and new Postgres for one cycle before retiring SQLite.

---

## Phase 3 — Permissions, Users, Teams, RBAC, Watchers

**Tasks**:
1. **Permission vocabulary**: seed `permissions` table from the list above. `role_permissions` table maps default bundles.
2. **Migrate `users.role`** from `admin/user` to `support/manager/principal/admin` (admin → admin, user → support).
3. **Add `teams` table** with `parent_team_id` self-FK to support **hierarchical nesting (teams within teams)** + `users.team_id`. Seed from current personnel structure. Manager-of-team scope expands to descendant teams via recursive CTE; helper `team_member_ids(session, team_id)` walks the tree.
4. **Add `requester_user_id` + `assignee_user_id`** to each tracker table (nullable initially). Backfill where free-text matches a known user. Keep free-text as snapshot.
5. **Add `ticket_watchers` table**: `(id, ticket_table TEXT, ticket_id INTEGER, user_id, added_by_user_id, added_at)`. Composite UNIQUE on (ticket_table, ticket_id, user_id).
6. **Implement `current_user.has_permission(name)` helper** backed by `role_permissions`.
7. **Implement `visible_tickets(session, user, model)` and `capability_visible(session, user)`** as separate code paths.
8. **Object-level auth**: every `/api/v1/<table>/<id>` fetch composes through scope. Returns 404 (not 403) for invisible.
9. **Mandatory permission tests** per role per endpoint; CI fails on missing test for any new endpoint.
10. **CSV export goes through scope; every export writes audit row**.
11. **Intake-form submissions get `source=intake_form`** and land in a **triage queue** (decision #10). Add a `triage_state` column on tickets (`new` | `triaged` | `assigned`) and a `/triage` UI for the designated triage owner. An `intake_default_assignee_user_id` setting (or per source/category routing rule) can auto-route specific submissions and skip the queue.

**Exit**:
- Each role sees exactly the right tickets and no others, including via API and CSV export
- Hostile lookups return 404 (not 403)
- `current_user.has_permission(...)` is the ONLY authorization predicate in route code
- Watchers can be added/removed; watched tickets show in support-tier scope
- Permission test coverage at 100% for tracker + capability endpoints

---

## Phase 4 — Capability Tracking Carved Out

- Own blueprint: `app/routes/capability.py` mounted at `/capability/...`
- Own templates: `templates/capability/*.html` not `templates/index.html`
- Own scope helper: `capability_visible(...)` (separate path, never composes)
- Own audit log discipline (longer retention; entries not self-deletable)
- Optional: separate Postgres schema (`capability.notes`) so a SQL bug in tickets cannot leak it
- Capability notes can NEVER be hard-deleted; soft-delete only via admin

---

## Phase 5 — Public Ticket IDs + Structured Audit Log

- Add `public_id` column with `BR-2026-####` pattern (sequence + year). Backfill existing rows.
- Refactor `activity_log`: `actor_user_id` (FK), `source` (web/api/email/telegram/maximus), `before_json`, `after_json`. Migrate existing rows with `actor_user_id=NULL, source='legacy'`.
- Update Telegram messages, email subjects, UI titles to use `public_id`.

---

## Phase 6 — Email Intake Idempotency + AI Retention Machinery (Conditional)

**Conditional**: only execute if reviving the email intake timer. If timer stays disabled, defer this whole phase.

**Tasks**:
- Add `email_intake_processed` table: `(message_id PK, body_hash, from_addr, subject, processed_at, task_id, result)`
- Check `message_id` (and as fallback `body_hash`) **before** processing
- Add nightly cron: purge `ai_triage_calls.raw_input` after `AI_RAW_INPUT_RETENTION_DAYS`, replace with `raw_input_sha256` only
- Add admin UI showing AI usage stats (calls per day, cloud-fallback rate, retention status)

**Required before** any future email-threading work in Phase 8.

---

## Phase 7 — Remove or Spin Out `personal_tasks`

**Default action: REMOVE.** 0 rows today. Maximus may not actually depend on it.

1. Confirm with Maximus operator: is `/api/v1/maximus/*` actively used? If no → delete table, delete routes, remove from `app/models/personal.py`. Coordinate Maximus-side update.
2. If yes → spin out as a tiny separate service (`maximus-tasks` on its own port, own SQLite, own systemd unit). Migrate rows + endpoints. Update Maximus to point at new service. Delete from TaskTrack.

Either way, TaskTrack ends up clean of personal-task surface area.

---

## Phase 8 — Service-Desk Features (Ongoing)

In approximate order of user pain:
- Attachments (S3/MinIO/R2; civil work needs drawings + redlines + PDFs)
- SLA timers (`first_response_at`, `resolved_at`, business hours)
- Email threading (outbound SMTP, `In-Reply-To` matching, per-ticket inbound aliases)
- Notifications layer (email + Telegram + in-app, per-user/per-event subscriptions)
- **Outlook (Microsoft Graph API) calendar integration** — replaces the Nexus-specific Radicale widget for the company product. Read-only "upcoming events" glance plus optional "ticket due → calendar event" sync.
- Saved views, reporting dashboards
- Knowledge base / canned responses

---

## Phase 9 — Optional: Re-evaluate Ticket-Table Unification

By now, RBAC, audit, attachments, SLA, notifications, watchers, assignment all live in shared infrastructure. Ask honestly: does collapsing the 5 tables into one still buy enough to justify the rewrite?

If yes → clean migration with the experience built up. If no → leave them as parallel tables with a shared base model. **The option is preserved either way.**

---

## UI Partials Policy (Applies During Phases 3-5)

When RBAC UI work touches `templates/index.html`, modest extraction into Jinja partials is **permitted**:
- `{% include 'partials/ticket_row.html' %}`, `{% include 'partials/filter_bar.html' %}`
- Extract repeated modal markup
- Split the inline `<script>` block into a few focused `.js` files (still vanilla, no bundler)

**Not allowed in this phase range**:
- Frontend framework (React/Vue/Svelte)
- Build step (webpack/vite/esbuild)
- Visual redesign
- SPA routing changes

Goal: reduce risk on RBAC UI changes by isolating permission-aware markup. Not a frontend rewrite.

---

## Security & Privacy Additions

| Concern | Phase | Notes |
|---|---|---|
| AI cloud fallback default-off | **1B** | env-driven; per-call audit row |
| AI raw-input retention setting | **1B** | setting only; purge cron in 6 |
| AI triage call audit table | **1B** | one schema addition outside Alembic |
| CSRF on session-auth APIs | **1B** | Flask-WTF or seasurf |
| Rate limit on intake forms | **1B** | per-profile caps |
| Telegram chat_id → user_id binding | **1C** | rejected if unbound |
| Capability intake removed from public forms | **1C** | regardless of profile |
| Object-level auth tests | **3** | required for every `/<table>/<id>` route |
| CSV export scoped + audited | **3** | `ticket.export` permission; row count logged |
| Soft delete | **4-5** | hard delete forbidden in company profile; capability never |
| Email intake idempotency | **6** | conditional on revival |
| Full AI retention purge cron | **6** | hash-only after N days |

---

## Specific "Do Not Do Yet" List

| Don't yet | Revisit in |
|---|---|
| Unify the 5 tracker tables into one | Phase 9 (optional) |
| Introduce React/Vue/any frontend framework | After Phase 8 if SPA grows beyond manageable |
| Add SLA timers, attachments, notifications, KB | Phase 8 |
| Add SSO (M365 / Google Workspace) | **Not in current plan** — auth is self-hosted DB-backed (decision #6). If revisited, post-launch only. |
| Multi-tenancy beyond `organization_id DEFAULT 1` slot | When a 2nd tenant exists |
| Frontend redesign or build step | After Phase 8 |
| Move to Postgres | Phase 2, **after** 1D-2 ships |
| Decouple Telegram bot from `app.py` | Phase 1C |
| Split `TASKTRACK_TOKEN` | Phase 1C |
| Build a "private mode" for personal items | Likely never if Phase 7 removes `personal_tasks` |
| Add `/api/v2/...` | Until v1 stable + breaking change justified |
| Touch `/api/maximus/*` semantics | Until Phase 7 coordinated with Maximus |
| Enable AI cloud fallback by default in company profile | Permanent — explicit override only |
| Keep Radicale calendar widget on in company profile | Permanent default-off. Replaced by Outlook (Microsoft Graph) integration in Phase 8. |
| Hard-delete tickets (esp. capability notes) | Soft-delete only from Phase 4/5 onward |
| Trust intake-form rows as production data | Until Phase 3 triage queue + assignment exists |
| Add **any** new features during Phases 0-1D-2 | Foundation only |
| Bulk-convert all routes to SQLAlchemy in one PR | 1D-2 is explicitly per-blueprint |
| Run schema mutations outside Alembic | After 1B's `ai_triage_calls` addition, never |
| Anonymous capability submissions via intake form | Permanent — capability route removed from intake in 1C |

---

## Open Decisions Still Requiring Owner Input

Most decisions resolved across two batches on 2026-04-26 — see the Decisions Locked In sections above for resolutions and cascading impact. Three items remain:

8. **`personal_tasks` future** — REMOVE (default) or spin out. Needs Maximus operator confirmation before Phase 7.
16. **Gunicorn worker count** — currently 2. Revisit after Phase 1B structured logging shows whether requests are queueing.
17. **Repo / systemd service rename to `br-task-tracker`** — deferred per-plan; revisit after Phase 2 (Postgres migration) ships cleanly.

Plus one TBD-by-firm slot to surface during Phase 1B / Phase 4:
- **#7 Compliance / retention requirements** — firm answer pending. Plan ships with sensible defaults (90/365 day AI retention, no auto-delete on tickets), all admin-configurable per category so the firm can tighten or relax them once policy lands.
