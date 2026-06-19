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

## Verification gate
Final: octopus-merge A+B into finish-stragglers → `pytest` full suite green → deploy lab → hand Josh promote.
