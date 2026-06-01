# TaskTrack Next Review Handoff

Updated: 2026-06-01

## Current State

TaskTrack is the current priority app. The recent work focused on making the app testable by Josh and making feedback records useful context for future Codex sessions.

Recent commits:

- `8ccf73a Summarize feedback context for triage`
- `4de4adc Improve feedback beta loop workflow`
- `6c40a5e Improve feedback attachment staging`
- `33afd81 Refine competency technical staff view`
- `ba0a5ab Add employee photos to competency views`

## Feedback Loop Workflow

Use `/feedback` as the source of truth for beta testing notes.

Status semantics:

- `New`, `Triaged`, `Planned`, `In Progress`: Codex queue.
- `Needs Info`, `Ready to Test`, `Fixed`: Josh/user verification queue.
- `Fixed` is not final. It means Codex implemented something and Josh should verify it.
- `Accepted`, `Closed`, `Won't Fix`, `Archived`: terminal states.
- Prefer `Archived` over deleting feedback so testing history remains available.

The feedback detail page now includes:

- Queue tabs.
- Discussion/comments.
- Activity/status history.
- Attachment upload with paste/drop/multiple files.
- Image previews.
- Captured context summary.
- Raw context JSON behind a disclosure.

## What Josh Should Test Next

During normal app use, capture small issues with the floating Feedback button.

Useful feedback includes:

- Visual issues with screenshots.
- Wording/copy changes.
- Navigation surprises.
- Form behavior problems.
- Missing employee/project selectors.
- Mobile/Chromebook layout issues.
- Anything that blocks using TaskTrack daily.

For each item, a rough title plus screenshot is enough. Add detail only when the expected behavior is not obvious.

## What Codex Should Do Next

At the start of the next review:

1. Check service health and git status.
2. Query feedback items first:

```bash
sqlite3 tracker.db "select id,status,priority,feedback_type,title,tab,component_label,updated_at from feedback_items order by updated_at desc;"
```

3. Prioritize active feedback:

```bash
sqlite3 tracker.db "select id,status,title,resolution_notes from feedback_items where status not in ('Accepted','Closed','Won''t Fix','Archived') order by updated_at desc;"
```

4. For each visual/behavior item, inspect attachments and `context_json` before changing code.
5. Mark items `In Progress` while fixing.
6. Mark items `Ready to Test` or `Fixed` with resolution notes and commit id after implementation.
7. Use `Needs Info` if the record is ambiguous.
8. Let Josh mark items `Accepted`, `Closed`, or `Archived` after testing.

## Verification Already Run

After the feedback upgrades:

- `python -m pytest -q` passed: `434 passed`.
- `tests/test_feedback.py` passed: `10 passed`.
- Inline feedback page JavaScript syntax check passed.
- `collab-tracker.service` restarted successfully.
- `/healthz` returned `ok`.

## Known Good Service State

Before restart on 2026-06-01:

- Git worktree was clean.
- Service was `active`.
- `/healthz` returned `ok`.

