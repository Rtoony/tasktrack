# TaskTrack — State of the Tool & Improvement Work Plan

**Date:** 2026-06-09
**Prepared by:** Claude Code (read-only audit — no code was changed)
**Scope:** Full survey of backend (~15k lines Python), frontend (26 templates incl. the 408KB SPA), security/ops, and live data in `tracker.db`.

This document is the durable backlog. Suggested workflow: pull items from here into
the `/feedback` queue (or hand directly to Codex) one phase at a time. It deliberately
respects the locked lane separation — **no CAD Project Setup metadata here; that's OrdoCAD's.**

---

## Part 1 — Current State Summary

### What TaskTrack is today

A Flask + SQLite + Gunicorn internal ops cockpit at `tasktrack.roonytoony.dev`
(systemd user unit `collab-tracker.service`, port 5050, nexus-ai tunnel, vault-injected
secrets). It has grown from the original single-file `app.py` scaffold into a genuinely
well-architected app:

- **App factory + 21 blueprints + 19 services**, Alembic-managed schema (27 revisions),
  fail-loud schema/bridge validation at boot, request-ID structured logging, CSP/HSTS
  headers, scoped API tokens (triage/bot/inbox), timing-safe token comparison.
- **24 tables**: 5 trackers (Project Work, CAD Dev, Training, Capability/Personnel,
  Internal Follow-up) + unified inbox, feedback loop, calendar, registry
  (6,915 projects / 5,392 sites / 36 employees), competency system (16 categories,
  evidence-based 0–3 scoring), comments, activity log, attachments (MinIO), links,
  managed dropdowns, report presets, telegram pairing.
- **Capture surfaces**: web forms (incl. branded B&R intake form), OCR printable forms,
  Telegram bot, email IMAP poller, mobile companion (PWA, capture/today/review),
  AI triage via LiteLLM (local-first, cloud fallback).
- **Bot APIs** consumed by Hermes: digest, agenda, task status, project notes, feedback.
  (Live access logs confirm Hermes hits `/api/v1/digest` + `/api/v1/agenda` daily.)
- **434 passing tests** across 42 test files. Working tree clean, main up to date with origin.

### The honest adoption picture (from live DB, 2026-06-09)

This is the gap that matters most. The *machine* is built; the *habit* isn't:

| Signal | Reality |
|---|---|
| Work tasks across all 5 trackers | **4 total** (2 CAD Dev, 2 Project Work, 0 Training) |
| Inbox items | 10 (3 Hermes, 4 web-form, 2 web-mobile, 1 Telegram) |
| Email-sourced tasks | **0 — the poller has never succeeded** (see P0-1) |
| AI-triage-committed tasks (`needs_review`) | **0 — the triage commit path has never been exercised** |
| Telegram pairings active | 0 |
| Calendar events | 1 |
| Links panel rows | 0 |
| Feedback items | 12 — **all bulk-marked "Accepted" on 06-07**; the live-queue workflow isn't being used iteratively |
| Activity rhythm | ~1 week of testing bursts (05-31 → 06-07), not daily operational use |
| Users | Josh (admin), Dyanna, 2 script accounts |

The ROADMAP's own 2026-05-30 pivot said it: *prove real daily use before more suite
architecture is built.* The work plan below is ordered around that.

### Live defect found during this audit

`tasktrack-email-intake.service` is **failing every 5 minutes** (timer active, service
failed). IMAP login to Proton Bridge (127.0.0.1:1143 as RtoonyClwBot@proton.me) returns
`no such user` and intermittently `too many login attempts`. Likely causes: Proton Bridge
not running / account not loaded, or the bridge password rotated since the vault item
`Nexus - Intake Mailbox` was created. Separately, per the original activation checklist
(`ops/EMAIL_INTAKE.md`), the Josh-side prerequisites were never completed: the Proton
filter (`+intake` → folder `Intake`) and Gmail/work-mail forwarding to
`RtoonyClwBot+intake@proton.me`. So even with auth fixed, nothing would arrive yet.

---

## Part 2 — Work Plan

Severity/effort legend: **S** < 2h · **M** half-day–2 days · **L** multi-day.
Items marked 🤝 need Josh personally (web UIs / habits Claude+Codex can't reach).

### Phase 0 — Stop the bleeding (this week)

| # | Item | Why | Effort |
|---|---|---|---|
| P0-1 | **Fix or pause email intake.** Diagnose Proton Bridge state on the AI PC; re-verify the bridge password vs vault item `Nexus - Intake Mailbox`; if not fixable now, `systemctl --user disable --now tasktrack-email-intake.timer` so it stops crash-looping (and hammering the bridge into `too many login attempts`). | Failing unit every 5 min for ~6 days; lockout risk affects Maximus email-ops which shares the account. | S |
| P0-2 | 🤝 **Complete email-intake prerequisites** (once P0-1 fixed): Proton filter `+intake → Folders/Intake` (do NOT mark read), Gmail (both) + work-mail forwarding to `RtoonyClwBot+intake@proton.me`. Checklist already written: `ops/EMAIL_INTAKE.md`. | Without forwarding, the flagship capture channel is dead code. | S |
| P0-3 | **Fix DB file permissions + relocate backups.** Several `tracker.db.bak-*` and `backups/*.db` copies are world-readable (644). `chmod 600` all; move ad-hoc backups out of the repo (e.g. `~/backups/tasktrack/`); keep only last N. Verify `tracker.db` is inside the restic/nexus-backup coverage set. | Local data exposure; backup sprawl with no rotation; unclear whether the only real protection is ad-hoc copies. | S |
| P0-4 | **Add `PRAGMA busy_timeout=5000`** in `app/db.py` next to the existing WAL/foreign-keys pragmas. | 3 gunicorn workers share one SQLite file; today a concurrent write throws `database is locked` immediately instead of waiting. Cheapest concurrency insurance available. | S |
| P0-5 | **Rate-limit token-authed POST endpoints** (`/api/v1/triage`, `/api/v1/inbox`). Intake web forms are limited (60/hr/IP) but the token endpoints are not — a leaked/looping client (e.g. the email poller itself) can spam unbounded. | Defense in depth; the email poller bug shows automated callers do misbehave. | S |
| P0-6 | **Prune merged `codev/*` branches + worktrees** (`git worktree list`; 7 codev branches exist, several already merged into main). | Known personal gotcha: unmerged-branch sprawl hides real work. | S |

### Phase 1 — Adoption sprint (the actual goal, next 2–3 weeks)

The tool's biggest gap is not a missing feature — it's that the capture→today→close loop
isn't part of the daily routine yet, and the features that *create* the daily pull
(reminders, saved views) are the two roadmap items with zero code.

| # | Item | Why | Effort |
|---|---|---|---|
| P1-1 | 🤝 **Commit to the 2-week daily-use trial** the ROADMAP already defines: every real work item enters via inbox/mobile-capture/Telegram; start the day on the Today/agenda view; close tasks in-app. The `adoption_metrics` service already measures this — review its numbers weekly. | Everything else in this plan is calibrated by what daily use reveals. | habit |
| P1-2 | **Reminders / notifications (Telegram first).** A small scheduled job (systemd timer, like the digest pattern) that DMs: tasks due today/overdue, calendar events with `reminder_date` hit, inbox items sitting unreviewed > N days. The `calendar_events.reminder_date` column already exists with no trigger logic. | #1 missing adoption driver; due dates currently do nothing. | M |
| P1-3 | **Exercise the AI-triage commit path end-to-end** and fix what breaks. Zero rows have ever had `needs_review=1`; preview works but commit→confirm has no production reps. Run 5–10 real messy inputs through paste + email + Telegram once channels are live. | The feature is the product's centerpiece and has never been used in anger. | S–M |
| P1-4 | 🤝 **Pair Telegram for real** (pairing code flow exists, `telegram_chat_access` has 0 rows) and use quick-capture for a week. | Capture friction is the make-or-break for ADHD-friendly intake. | S |
| P1-5 | **Saved views** (persist filter sets per user — the `report_presets` table pattern already exists, extend to tracker tabs: "My overdue", "Due this week", "Needs review"). | Roadmap item with zero code; converts the dashboard from demo to cockpit. | M |
| P1-6 | **Use the feedback queue as a live queue.** Re-adopt the status workflow from `NEXT_REVIEW_HANDOFF.md` (New→Triaged→…→Ready to Test→Accepted) instead of batch-accepting. Seed it from this document's Phase 1–2 items so Codex sessions have a real queue. | The feedback loop is the engine of the Codex co-dev workflow; bulk-accept starves it. | habit |

### Phase 2 — Finish the half-built features (next 1–2 months)

Each of these has schema and/or backend already shipped but no (or partial) UI surface.

| # | Item | Existing footing | Effort |
|---|---|---|---|
| P2-1 | **Project workspace MVP** — `/project/<number>` detail panel: overlay metadata, linked tasks across trackers, internal notes, map snippet, report actions. | `project_workspace.py` service (213 lines) + bot note-data endpoint + FK spine already exist; no human UI/route. | L |
| P2-2 | **Calendar module v1** — month/week UI, create events from tasks (due dates auto-appear), meeting-prep linkage (`related_table`/`related_id` columns already there), feeds P1-2 reminders. | Table + CRUD routes + agenda service exist; 1 event ever created. | L |
| P2-3 | **Report engine maturation** — saved presets actually used (table is empty), scheduled weekly management report delivered via Telegram/email, batch meeting packets. | 10 report surfaces + presets table + admin shortcuts already shipped. | M–L |
| P2-4 | **Hyperlinks panel UI** — surface the existing `links` service (smart URL recognizer, 165 lines, fully tested) as the planned sibling widget of attachments. | Backend + tests done; 0 UI. | S–M |
| P2-5 | **Attachment image/PDF preview thumbnails** (lightbox for images, first-page render or icon+metadata for PDFs; feedback page already does image previews — reuse that pattern in record modals). | MinIO presigned URLs exist; feedback.html has the pattern. | M |
| P2-6 | **Competency → Training bridge** — from a capability gap / incident, one-click "create training task" with fields carried over via the existing BRIDGE_MAP machinery. | Bridges service is idempotent + declarative already; this is one more bridge entry + a button. | S–M |
| P2-7 | **Hermes two-way loop** — Hermes already reads digest/agenda and can close tasks; add change-notifications back to Hermes (or a daily "what changed" delta endpoint) so closures/priority changes flow into briefings. | Bot-scoped endpoints + digest infra exist. | M |

### Phase 3 — Code health & security hardening (background, steady drip)

Frontend (the big one):

| # | Item | Detail | Effort |
|---|---|---|---|
| P3-1 | **Hold the line on the Carbon/React redesign as the strategic fix** for the 408KB `index.html` (5,850 lines inline JS + 2,850 lines inline CSS, 12 tabs, zero modularity). Don't piecemeal-refactor the SPA *and* rebuild it — pick the redesign lane (artifacts ready in `~/tasktrack-design/`), schedule it **after** the adoption sprint proves which views matter. | A rewrite before adoption data = redesigning the wrong screens. | decision |
| P3-2 | **Interim: extract a shared report base.** `templates/base_report.html` + `static/css/report-base.css` — the 7 report templates each duplicate ~240 lines of identical CSS/HTML shell (only the accent color differs). Worth doing even with a redesign pending; reports are likely to survive it. | 8h per the audit; kills the worst duplication. | M |
| P3-3 | **Document the `br-intake.jsx` build step.** A 400KB minified React bundle is committed with no webpack/esbuild config or Makefile target — it cannot currently be rebuilt from source in-repo. | One stale bundle away from an unfixable form. | S |
| P3-4 | **Migrate brittle `innerHTML` string-building to safe DOM APIs** in index.html hot spots (lines ~3350/3379/4354/4394/6801). Currently safe (`esc()` is used consistently) but the pattern invites the first missed escape. | Skip if redesign lands first. | M |

Backend:

| # | Item | Detail | Effort |
|---|---|---|---|
| P3-5 | **FK coercion + name-matching silent failures** (`app/services/tickets.py`): `_coerce_fk_columns` silently nulls malformed FK ids; `enrich_with_fks` LIMIT-1s on duplicate case-insensitive employee names. Log warnings at minimum; raise on ambiguity ideally. | "Wrong person linked to incident" is the bug class this prevents. | S–M |
| P3-6 | **Generate search SQL from model introspection** (`app/routes/api.py` ~231–252): six hand-maintained UNION queries silently miss columns added later. | Prevents silent search blind spots. | M |
| P3-7 | **Centralize visibility logic** — `record_visible_to_user` covers most, but calendar/personnel/reports re-implement variants. One permission module, one test suite. | New features keep copying whichever pattern they find first. | M |
| P3-8 | **`person_id`/`person_ids` consistency validation** on personnel_issues (service-layer check or constraint) now that person is nullable for 0-person incidents. | Prevents inconsistent multi-person incident states. | S |
| P3-9 | **Presigned-URL TTL env var** (`TASKTRACK_ATTACHMENT_URL_TTL`, default 5 min) — hardcoded 5 min will bite remote/slow-link users. | One-liner with a default. | S |
| P3-10 | **Triage call retry/backoff** — LiteLLM call is one-shot with a 90s timeout; add a single retry before falling back to cloud, and honor the existing-but-unused `TRIAGE_TIMEOUT_S`. | Email-sourced inputs are lost on transient gateway blips. | S |

Security/ops:

| # | Item | Detail | Effort |
|---|---|---|---|
| P3-11 | **Retire legacy `TASKTRACK_TOKEN`** (accepted across all scopes with only a deprecation log). Set a hard date, rotate consumers (Maximus, Hermes, email poller, telegram bot) to scoped tokens, then remove. | Single-token blast radius defeats the scoping work already done. | M |
| P3-12 | **Password minimum 6 → 12 chars** (`app/routes/auth.py`), and confirm `SESSION_COOKIE_SECURE` is on in production env (it defaults off). | Two-user app, but it's internet-reachable. | S |
| P3-13 | **Decide `intake_auth_required`** — currently a no-op (open intake forms on a public hostname, rate-limited only). Recommend: require app login for v1, revisit if office staff ever submit directly. | Open question flagged since the BR-form handoff. | decision + S |
| P3-14 | **Move `tracker.db` out of the repo root** via the existing `DB_PATH` env (e.g. `~/data/tasktrack/tracker.db`), update the systemd unit, and add `StartLimitIntervalSec`/`StartLimitBurst` to the unit so a vault outage doesn't restart-spam. | Repo root currently holds live DB + WAL + 8 stale .bak copies. | S–M |
| P3-15 | **Service watchdog parity** — add `tasktrack-email-intake` (and the telegram bot service if used) to the unlock/secret-restart lists so they survive reboots (the known `gotcha_unlock_list_reboot_death` class), and add a failed-unit alert so a 6-day crash-loop like P0-1 gets noticed in hours, not audits. | This audit found the failure by accident. | S–M |

### Phase 4 — Standing decisions (decide once, write down, stop revisiting)

1. **OrdoCAD lane stays locked.** No jurisdictions/stakeholders/sheet-index/boundary
   metadata in TaskTrack (`~/projects/ordocad/docs/SEPARATION_AND_DEPLOY_PLAN.md`).
2. **SQLite stays** until reporting/geometry actually hurts. With busy_timeout (P0-4)
   and current volumes (4 tasks!), Postgres talk is premature.
3. **Frontend redesign timing**: after the adoption sprint (see P3-1). The redesign
   replaces the SPA; the ~22 standalone report/intake pages and the whole `/api/v1`
   backend carry forward unchanged.
4. **Tracker renames** ("firm-shop framing no longer load-bearing" per ROADMAP):
   decide during the adoption sprint when real usage shows what the queues actually are.
5. **Local-only vs cloud-fallback triage**: keep the fallback until P1-3 generates real
   data on local-model JSON reliability; revisit then.

---

## Part 3 — Suggested sequencing at a glance

```
Week 1        P0-1..P0-6 (stabilize)  +  start P1-1 trial
Weeks 1–3     P1-2 reminders → P1-4 telegram → P1-3 triage reps → P1-5 saved views
              P1-6: feed this plan into /feedback as the live Codex queue
Weeks 3–8     P2 items, ordered by what the trial proves you reach for:
              workspace (P2-1) and calendar (P2-2) first if reports/meetings dominate;
              links + previews (P2-4/5) if record-keeping dominates
Background    one P3 item per Codex session as warm-up
After trial   P3-1/P4-3 redesign go/no-go with real usage data in hand
```

**The one-sentence thesis:** TaskTrack's engineering is ahead of its adoption — the
highest-leverage "improvements" are the two never-built habit drivers (reminders,
saved views), turning on the capture channels that are wired but dark (email, Telegram,
AI commit), and then letting two weeks of real daily use decide which of the half-built
modules (workspace, calendar, reports) gets finished first.
