"""Triage Outcomes report — Triage Phase 3.

The evaluation corpus accrues for free: every promoted/auto-filed inbox
item carries the ADVISORY suggestion it was given (suggested_table +
suggestion_json), the target it actually landed in (promoted_to_table /
promoted_to_id), and timestamps (created_at, suggested_at). This report
turns that corpus into the numbers that decide which templates/models
*graduate* to auto-file (Phase 2b) and which prompts/templates need work:

    per source / model (= "rule:<template>" for deterministic templates,
    or the LLM model id for AI suggestions):
      - volume                  (# of assigned items it suggested for)
      - target accuracy         (suggested_table == chosen target)
      - field accuracy          (1 - mean field edit-rate on prefills:
                                 how often the human kept the drafted value)
      - time-to-assignment      (mean / median seconds from capture to file)

ADVISORY-DATA ONLY — this is a pure read over inbox_items (+ the target
rows, to diff drafted vs. final fields). It writes nothing and never
mutates a suggestion. Read it, decide, then flip a template's
Trust.auto_file by hand (the deliberate graduation act).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ALLOWED_TABLES
from ..models import InboxItem
from .tickets import TABLE_MODELS

# Bookkeeping keys never part of a drafted suggestion — excluded from the
# field-accuracy diff (mirrors the suggestion contract elsewhere).
_FIELD_EXCLUDES = ("needs_review", "source", "ai_raw_input", "ai_model",
                   "status", "created_at", "updated_at", "id")


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _suggestion_source(suggestion: dict) -> str:
    """The label we group outcomes by: the model id, which for deterministic
    templates is "rule:<template>" and for AI suggestions is the LLM id."""
    model = str(suggestion.get("model") or "").strip()
    return model or "unknown"


def _drafted_fields(suggestion: dict) -> dict:
    fields = suggestion.get("fields")
    if not isinstance(fields, dict):
        return {}
    return {
        k: v for k, v in fields.items()
        if k not in _FIELD_EXCLUDES and v is not None and str(v).strip() != ""
    }


def _field_edit_stats(sess: Session, suggestion: dict, item: InboxItem) -> tuple[int, int]:
    """Compare drafted fields against the FINAL record's values.

    Returns (kept, compared): how many drafted fields the human kept
    verbatim, out of how many drafted fields we could compare. A field is
    "compared" only when it exists on the final target row; "kept" when the
    final value equals the drafted value (string-normalized). Returns (0, 0)
    when the final record can't be resolved (no signal, excluded by caller).
    """
    target = (item.promoted_to_table or "").strip()
    Model = TABLE_MODELS.get(target)
    if Model is None or item.promoted_to_id is None:
        return 0, 0
    record = sess.get(Model, item.promoted_to_id)
    if record is None:
        return 0, 0

    drafted = _drafted_fields(suggestion)
    valid_cols = {c.name for c in Model.__table__.columns}
    kept = compared = 0
    for key, drafted_val in drafted.items():
        if key not in valid_cols:
            continue
        compared += 1
        final_val = getattr(record, key, None)
        if str(final_val or "").strip() == str(drafted_val or "").strip():
            kept += 1
    return kept, compared


def _new_accumulator() -> dict:
    return {
        "volume": 0,
        "target_correct": 0,
        "fields_kept": 0,
        "fields_compared": 0,
        "assignment_seconds": [],
    }


def _finalize(acc: dict) -> dict:
    volume = acc["volume"]
    secs = acc["assignment_seconds"]
    target_accuracy = (acc["target_correct"] / volume) if volume else None
    field_accuracy = (
        acc["fields_kept"] / acc["fields_compared"]
        if acc["fields_compared"] else None
    )
    return {
        "volume": volume,
        "target_correct": acc["target_correct"],
        "target_accuracy": round(target_accuracy, 4) if target_accuracy is not None else None,
        "fields_kept": acc["fields_kept"],
        "fields_compared": acc["fields_compared"],
        "field_accuracy": round(field_accuracy, 4) if field_accuracy is not None else None,
        "time_to_assignment_seconds": {
            "count": len(secs),
            "mean": round(statistics.mean(secs), 1) if secs else None,
            "median": round(statistics.median(secs), 1) if secs else None,
        },
    }


def triage_outcomes_report(sess: Session, *, days: int = 90,
                           limit: int = 1000) -> dict:
    """Measure suggestion quality over assigned inbox items.

    Considers inbox items that were SUGGESTED (have a suggestion_json) and
    then ASSIGNED (promoted_to_table set) within the window, grouped by the
    suggestion source (model / rule:<template>). Auto-filed items count too
    — they're promoted rows like any other.
    """
    days = max(1, min(int(days or 90), 3650))
    limit = max(1, min(int(limit or 1000), 5000))
    since = datetime.now() - timedelta(days=days)

    stmt = (
        select(InboxItem)
        .where(InboxItem.suggestion_json.is_not(None))
        .where(InboxItem.promoted_to_table != "")
        .order_by(InboxItem.created_at.desc())
        .limit(limit)
    )

    by_source: dict[str, dict] = {}
    by_target: dict[str, dict] = {}
    overall = _new_accumulator()
    considered = 0
    skipped_unparseable = 0

    for item in sess.scalars(stmt).all():
        created = _parse_dt(item.created_at)
        if created is None or created < since:
            continue
        try:
            suggestion = json.loads(item.suggestion_json)
        except (ValueError, TypeError):
            skipped_unparseable += 1
            continue
        if not isinstance(suggestion, dict):
            skipped_unparseable += 1
            continue

        considered += 1
        source = _suggestion_source(suggestion)
        suggested_target = (suggestion.get("target_table") or "").strip()
        chosen_target = (item.promoted_to_table or "").strip()
        target_correct = 1 if suggested_target and suggested_target == chosen_target else 0

        # time-to-assignment: capture -> the promote/auto-file. We don't store
        # the assignment timestamp separately, so use updated_at (set at
        # promote/auto-file) minus created_at as the best available proxy.
        assigned_at = _parse_dt(item.updated_at)
        tta = (assigned_at - created).total_seconds() if assigned_at else None

        kept, compared = _field_edit_stats(sess, suggestion, item)

        for bucket_key, registry in (
            (source, by_source),
            (chosen_target or "unknown", by_target),
        ):
            acc = registry.setdefault(bucket_key, _new_accumulator())
            acc["volume"] += 1
            acc["target_correct"] += target_correct
            acc["fields_kept"] += kept
            acc["fields_compared"] += compared
            if tta is not None and tta >= 0:
                acc["assignment_seconds"].append(tta)

        overall["volume"] += 1
        overall["target_correct"] += target_correct
        overall["fields_kept"] += kept
        overall["fields_compared"] += compared
        if tta is not None and tta >= 0:
            overall["assignment_seconds"].append(tta)

    sources = [
        {"source": key, **_finalize(acc)}
        for key, acc in by_source.items()
    ]
    sources.sort(key=lambda r: r["volume"], reverse=True)
    targets = [
        {"target_table": key, "label": ALLOWED_TABLES.get(key, {}).get("label", key),
         **_finalize(acc)}
        for key, acc in by_target.items()
    ]
    targets.sort(key=lambda r: r["volume"], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filters": {"days": days, "limit": limit},
        "summary": {
            "considered": considered,
            "skipped_unparseable": skipped_unparseable,
            **_finalize(overall),
        },
        "by_source": sources,
        "by_target": targets,
    }


# ── CSV export ─────────────────────────────────────────────────────────────

OUTCOMES_CSV_FIELDS = [
    "group_kind", "group", "volume", "target_correct", "target_accuracy",
    "fields_kept", "fields_compared", "field_accuracy",
    "ttassign_count", "ttassign_mean_s", "ttassign_median_s",
]


def _csv_row(group_kind: str, group: str, row: dict) -> dict:
    tta = row.get("time_to_assignment_seconds") or {}
    return {
        "group_kind": group_kind,
        "group": group,
        "volume": row.get("volume", 0),
        "target_correct": row.get("target_correct", 0),
        "target_accuracy": row.get("target_accuracy", ""),
        "fields_kept": row.get("fields_kept", 0),
        "fields_compared": row.get("fields_compared", 0),
        "field_accuracy": row.get("field_accuracy", ""),
        "ttassign_count": tta.get("count", 0),
        "ttassign_mean_s": tta.get("mean", ""),
        "ttassign_median_s": tta.get("median", ""),
    }


def triage_outcomes_csv(packet: dict) -> str:
    import csv
    import io

    from .csv_safe import csv_safe

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=OUTCOMES_CSV_FIELDS)
    writer.writeheader()
    writer.writerow({k: csv_safe(v) for k, v in
                     _csv_row("overall", "ALL", packet.get("summary", {})).items()})
    for row in packet.get("by_source", []):
        writer.writerow({k: csv_safe(v) for k, v in
                         _csv_row("source", row.get("source", ""), row).items()})
    for row in packet.get("by_target", []):
        writer.writerow({k: csv_safe(v) for k, v in
                         _csv_row("target", row.get("target_table", ""), row).items()})
    return output.getvalue()


__all__ = [
    "OUTCOMES_CSV_FIELDS",
    "triage_outcomes_csv",
    "triage_outcomes_report",
]
