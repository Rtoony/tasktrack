# STATUS — "zero stragglers tonight" finish run (2026-06-18)

**Goal:** clear every outstanding TaskTrack item from the interview, leave a clean reviewable
branch + a gated promote script for Josh. Build to Josh's own taste (NOT "older-engineer
accessibility" — that framing was accidental). Streamline / fewer-clicks / dark-consistent.

**Base:** main `acdeba4`. My stream: `claude/finish-stragglers` (worktree ~/projects/ct-finish).

## Work partition (DISJOINT files — octopus-merge at end)
- **Agent A — Weekly W8+W9** → branch `claude/finish-weekly` (~/projects/ct-weekly)
  files: app/services/weekly.py, app/routes/weekly.py, templates/weekly.html
- **Agent B — Timezone B2** → branch `claude/finish-timezone` (~/projects/ct-timezone)
  files: app/services/incident_reports.py, app/services/intake_reports.py
- **Me (main stream)**: competency B2 (1-line, competency_interview.py:132-133), #54 map polish
  (index.html), #55 reMarkable build (new files + app factory hook + index.html attachment hook),
  #39 data-reuse plan (doc), reconcile script for Josh, ruff sweep (AFTER merge).

## Items
- [ ] B2 competency clamp→null — competency_interview.py:132-133 (`max(0,min(3,score))` → `None`). MINE.
- [ ] #54 Project Map — assess + polish (index.html "map" tab, Leaflet). MINE.
- [ ] #55 reMarkable — BUILD screenshot/attachment→tablet via inkpress/rmcloud.py + vault `Nexus - rmfakecloud`. MINE.
- [ ] #39 — write data-reuse plan doc. MINE.
- [ ] #37/#35 — close as out-of-scope (Hermes co-dev autopilot covers). Reconcile script.
- [ ] W8 last-week preset / week_offset + WoW delta. AGENT A.
- [ ] W9 surface "Stuck (31d+): N" by default + net-backlog framing. AGENT A.
- [ ] Timezone B2 — intake_reports + incident_reports window off UTC; fix days_open UTC mix. AGENT B.
- [x] D3 — ALREADY SHIPPED by WS-3 R3 (competency_report.html:209-210 shows N/M tasks). Verified no-op.
- [ ] Reconcile #53/#23/#43/#51/#37/#35 → Accepted/Closed (gated script for Josh).
- [ ] ruff --fix sweep (run on merged tree, last).

## Invariants — must NOT break
- Zero-disk secrets: Vaultwarden via nexus-inject ONLY. Never write .env/hardcode. New cred → `Nexus - <Service>` item.
- is_overdue_value() (app/services/tickets.py) is the single source of truth for overdue. Don't reimplement in SQL.
- Classifier-gated (Josh runs): promote.sh, prod-DB reconcile writes, git push, prod migrations.
- Full-file-in/full-file-out for agents. No TODO/stub in a closed phase. Run pytest before claiming done.
- Do NOT touch project_reports.py for timezone (CalendarEvent.start_at is user-local — already correct).

## Progress (live)
- [x] B2 competency clamp→null — commit e261f7c.
- [x] #54 map legend — commit 38b97dc (render-smoke OK).
- [x] #55 reMarkable — commits 37b93ee (backend) + 38b97dc (UI); 13 tests pass, ruff clean.
- [x] #39 data-reuse plan — ~/reports/tasktrack-39-data-reuse-plan-2026-06-18.md.
- [x] Agent A Weekly W8/W9 — merged (262a020), branch a11fe97, 54 weekly tests pass.
- [x] Agent B Timezone B2 — merged (43bd42e), branch 2b1eafb, 83 tests pass, project_reports untouched.
- [x] Reconcile script (mixed statuses) — ~/reconcile-tasktrack-feedback-2026-06-18.py (dry-run-safe).
- [~] Full suite gate — RUNNING (/tmp/finish-fullsuite.log).
- [~] Adversarial review over merged diff — RUNNING (workflow wf_eb491cee-f76).
- [ ] Apply any mustFix → lab deploy → hand Josh promote.

## Verification gate — ALL PASSED ✅
- Full suite: **768 passed, 0 failed** (7m37s) on merged tree.
- Adversarial review (5 dims + verifier, 8 agents): **0 must-fix**; 3 confirmed low/non-blocking
  (gif/webp button mismatch=unreachable+fails-safe; sync-upload worker-hold=gunicorn reaps 120s;
  Pillow-in-requirements=APPLIED, commit 3a58ffd).
- Lab deploy: healthy at :3410; /, /weekly?week_offset=1, /api/v1/weekly serve; /remarkable route
  registered (403 CSRF, not 500). reMarkable auth+connect verified READ-ONLY (login+list OK; no upload).
- HEAD: claude/finish-stragglers @ 3a58ffd. Promote: ~/promote-tasktrack-finish-2026-06-18.sh (Josh runs).

## Runtime note (#55)
Prod collab-tracker.service has bw on PATH + reads /dev/shm/nexus_session → reMarkable works when
vault unlocked (degrades to clean 503 "vault locked" otherwise). RMCLOUD_* NOT injected into svc env
(not needed; client falls back to vault session). Optional hardening: add RMCLOUD_USER/PASS to the
"TaskTrack" vault group + nexus-svc-inject (classifier-gated; not required).
