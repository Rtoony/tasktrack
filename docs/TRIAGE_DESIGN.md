# Triage + Assignment — Design of Record

**Date:** 2026-06-12 · **Owner:** Josh · **Status:** Phase 1 in build, Phases 2–3 planned

## The model

Triage is the single dump-ground for all capture (B&R submission form, email,
Telegram, mobile, paste, OCR). Nothing is categorized at capture time. The
SYSTEM proposes — category + drafted fields + confidence — and the HUMAN has
final say at assignment, except where a trusted pattern has *earned* bypass.

```
capture (any source)
   └─> inbox item
        ├─ template match? ──> deterministic suggestion (rule:<template>)
        │       └─ trusted + complete + above threshold? ──> AUTO-FILE
        ├─ else ──> AI classifier suggestion (model, confidence)
        └─> Triage view: suggestion chip → Assignment modal → tracker record
                                  (no needs_review — a human just reviewed it)
```

## Phase 1 — Unification (in build 2026-06-12)

- `inbox_items` + `suggested_table` / `suggestion_json` / `suggested_at`.
- `suggestion_json`: `{target_table, category, confidence: high|medium|low,
  fields: {...}, model, rationale}`.
- `run_classify()`: AI picks the target (CAD Dev, Project Task, Training,
  Incident Report/personnel_issues, personal_items+category) and drafts fields.
- `POST /api/v1/inbox/<id>/suggest` + best-effort auto-suggest on capture
  (`INBOX_AUTO_SUGGEST`, default on).
- B&R form submissions seed a deterministic `rule:request-type` suggestion at
  capture (template #1), then AI refines fields in the background.
- Assignment modal: suggested target pre-selected, prefilled editable fields,
  required fields enforced, AI rationale shown. Promote never writes
  `needs_review`; activity logs "assigned to X (AI suggested Y)" on override.
- Email intake lands in the inbox (no more auto-committed CAD Dev tasks).
- Express lane (dashboard Capture card, target pre-chosen) stays as-is.

## Phase 2 — Intake Templates (the Submission Forms of ingestion)

A registry of known input shapes that parse deterministically — mirror image
of the Submission Forms on the external end. Prototypes already in-tree:
`ocr_forms.py` PRINTABLE_REQUEST_FORMS (paper → target_table + parser) and the
Phase-1 request-type map.

Each template declares:

| Field | Meaning |
|---|---|
| `name` | e.g. `email-project-tagged`, `telegram-cad-prefix` |
| `match` | source + pattern (subject regex, body marker, prefix syntax) |
| `parse` | deterministic field extraction (project #, requested_by, due…) |
| `route` | target_table (+ category for personal_items) |
| `trust` | `auto_file: bool`, `min_confidence`, required-fields-complete gate |

Candidate first templates: `[####.##]`-tagged email subjects → project task
with parsed project number; `cad:` Telegram prefix → CAD Dev; forwarded
"Project Set-up" emails → project task. Unmatched input falls through to the
AI classifier. Storage: start as a Python registry module (versioned, tested),
graduate to a managed table + admin UI if Josh edits them often.

## Phase 2b — Auto-file (confidence-gated triage bypass)

Auto-file ONLY when ALL hold:
1. Suggestion source is a deterministic template with `auto_file: true`, OR an
   AI suggestion with confidence ≥ threshold on a source explicitly trusted.
2. Every required field of the target is present in the drafted payload.
3. Global kill-switch on (`INBOX_AUTO_FILE`, default OFF until Phase 3 data
   justifies per-template enablement).

Auto-filed records go through the SAME assignment code path, tagged
`auto-file:<template>` in the activity log + source. The inbox item is
archived with the link, like a human promote. Always auditable, easy to revert
to manual per template.

## Phase 3 — Outcomes & refinement loop

The evaluation corpus already accrues: suggestion (kept after assignment),
chosen target, field edits, activity log, timestamps, archived inbox items.

Build a **Triage Outcomes** report: per source/template/model —
suggestion-accuracy (suggested == chosen), field edit-distance on prefills,
time-to-assignment, volume. This is the dashboard that decides which templates
graduate to auto-file (Phase 2b) and which prompts/templates need refinement.
Revisit cadence: after ~1 month of real intake volume.

## Invariants

- Zero-disk secrets; no new tokens on disk.
- Suggestions are advisory until a pattern *earns* auto-file via Phase-3 data.
- The express lane and OCR direct paths keep their `needs_review`/confirm
  machinery until absorbed deliberately.
- External submitters never see any of this — Submission Forms remain the
  clean front door; templates + classifier do the routing behind it.
