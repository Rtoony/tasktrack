"""Cross-tracker bridge service (Phase 3).

Generalizes the existing inbox→tracker promote pattern. Given a source
(table, id) and a target table, builds a payload using BRIDGE_MAP's
field-carryover rules, applies any overrides from the caller, and
inserts via the standard `create_direct_record` so validation +
enrichment + audit log all fire naturally.

Idempotency: callers may pass an opaque `idempotency_key`. If
activity_log already records a "bridged_to" row with the same key
under the source, the existing target id is returned instead of a
fresh insert.
"""
from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..config import ALLOWED_TABLES, BRIDGE_MAP
from ..models import ActivityLog, to_dict
from .audit import log_activity
from .tickets import TABLE_MODELS, create_direct_record


class BridgeError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def get_targets_for(src_table: str) -> list[dict]:
    """List the allowed target tables for a given source. Used by the UI
    to populate the Promote/Escalate dropdown."""
    spec = BRIDGE_MAP.get(src_table, {})
    return [
        {
            "target": tgt,
            "label": rule.get("label", f"Promote to {tgt}"),
            "required_overrides": rule.get("required_overrides", []),
        }
        for tgt, rule in spec.items()
    ]


def _find_existing_target(sess: Session, src_table: str, src_id: int,
                          tgt_table: str, key: str) -> int | None:
    """Look for a prior bridge with the same idempotency key. Returns
    the existing target id if found, else None."""
    if not key:
        return None
    # The bridge writes activity_log on the SOURCE side with
    # action="bridged_to:<tgt_table>:<key>" and new_value=<tgt_id>.
    marker = f"bridged_to:{tgt_table}:{key}"
    row = sess.scalar(
        select(ActivityLog).where(and_(
            ActivityLog.table_name == src_table,
            ActivityLog.record_id == src_id,
            ActivityLog.action == marker,
        )).limit(1)
    )
    if row is None:
        return None
    try:
        return int(row.new_value)
    except (TypeError, ValueError):
        return None


def bridge_record(sess: Session, src_table: str, src_id: int,
                  tgt_table: str, overrides: dict | None = None,
                  idempotency_key: str = "") -> tuple[int, dict]:
    """Build and insert the bridged target row.

    Returns (target_id, target_dict). Raises BridgeError on bad input.
    Caller is responsible for sess.commit().
    """
    overrides = overrides or {}

    if src_table not in BRIDGE_MAP or tgt_table not in BRIDGE_MAP[src_table]:
        raise BridgeError(
            f"no bridge configured from {src_table} to {tgt_table}", 400
        )
    rule = BRIDGE_MAP[src_table][tgt_table]

    src_model = TABLE_MODELS.get(src_table)
    tgt_model = TABLE_MODELS.get(tgt_table)
    if src_model is None or tgt_model is None:
        raise BridgeError(
            f"unknown table in bridge: {src_table!r}/{tgt_table!r}", 400
        )

    src_row = sess.get(src_model, src_id)
    if src_row is None:
        raise BridgeError(f"{src_table}#{src_id} not found", 404)
    src = to_dict(src_row) or {}

    # Idempotency short-circuit.
    existing_id = _find_existing_target(
        sess, src_table, src_id, tgt_table, idempotency_key,
    )
    if existing_id is not None:
        existing = sess.get(tgt_model, existing_id)
        if existing is not None:
            return existing_id, to_dict(existing)

    # Build the payload: carry, then defaults, then overrides win.
    payload: dict = {}
    for sf, tf in rule.get("carry", {}).items():
        if sf in src and src[sf] not in (None, ""):
            payload[tf] = src[sf]
    payload.update(rule.get("defaults", {}))
    payload.update(overrides)

    # Title template — formatted against the source row, then placed
    # in `title_field` unless overridden.
    tmpl = rule.get("title_template")
    title_field = rule.get("title_field", "title")
    if tmpl and not payload.get(title_field):
        try:
            payload[title_field] = tmpl.format(**src)
        except (KeyError, IndexError):
            payload[title_field] = tmpl  # template referenced missing field

    # Required-overrides + target's own required-fields gate.
    for req in rule.get("required_overrides", []):
        if not str(payload.get(req, "")).strip():
            raise BridgeError(
                f"override required: {req!r} (not in carry map)", 400,
            )
    for req in ALLOWED_TABLES[tgt_table].get("required", []):
        if not str(payload.get(req, "")).strip():
            raise BridgeError(
                f"target {tgt_table!r} requires field {req!r} — "
                f"provide it via the overrides payload", 400,
            )

    new_id, error = create_direct_record(
        sess, tgt_table, payload,
        source_name=f"bridge:{src_table}#{src_id}",
        action="bridged_from",
        action_detail=f"{src_table}#{src_id}",
    )
    if error:
        raise BridgeError(error, 400)

    # Mark the source with the bridge link. We include the idempotency
    # key in the action string so re-runs with the same key short-circuit.
    marker = f"bridged_to:{tgt_table}"
    if idempotency_key:
        marker = f"{marker}:{idempotency_key}"
    log_activity(sess, src_table, src_id, marker,
                 field="bridge", new=str(new_id))

    target = to_dict(sess.get(tgt_model, new_id))
    return new_id, target
