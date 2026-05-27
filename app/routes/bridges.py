"""Cross-tracker bridge endpoints (Phase 3).

- GET  /api/v1/bridge/<src_table>/targets   — list allowed targets
                                              + their required overrides
- POST /api/v1/bridge/<src_table>/<src_id>/<tgt_table>
       body: { "overrides": {...}, "idempotency_key": "..." }

Both endpoints require a logged-in user. Bridges involving sensitive
capability/personnel records require admin access because carry-over
fields can copy restricted narratives into lower-sensitivity trackers.
"""
from flask import Blueprint, g, jsonify, request, session

from ..auth import login_required
from ..db import get_session
from ..services.bridges import BridgeError, bridge_record, get_targets_for
from ..services.tickets import TABLE_MODELS, can_view_record_detail

bp = Blueprint("bridges", __name__)

SENSITIVE_BRIDGE_TABLES = {"personnel_issues"}


def _rid():
    return g.get("request_id", "-")


def _is_admin() -> bool:
    return session.get("user_role") == "admin"


def _sensitive_bridge(src_table: str, tgt_table: str | None = None) -> bool:
    if src_table in SENSITIVE_BRIDGE_TABLES:
        return True
    return tgt_table in SENSITIVE_BRIDGE_TABLES


def _source_detail_visible(sess, src_table: str, src_id: int) -> bool | None:
    """Return None for unknown tables so bridge_record can keep its 400s."""
    Model = TABLE_MODELS.get(src_table)
    if Model is None:
        return None
    row = sess.get(Model, src_id)
    if row is None:
        return False
    return can_view_record_detail(
        src_table, row, session.get("user_id"), is_admin=_is_admin()
    )


@bp.route("/api/v1/bridge/<src_table>/targets", methods=["GET"])
@login_required
def list_bridge_targets(src_table):
    """Return the allowed (target, label, required_overrides) triples
    for a given source table. Used to populate the UI's promote dropdown."""
    targets = get_targets_for(src_table)
    if not _is_admin():
        targets = [
            row for row in targets
            if not _sensitive_bridge(src_table, row.get("target"))
        ]
    return jsonify(targets)


@bp.route("/api/v1/bridge/<src_table>/<int:src_id>/<tgt_table>", methods=["POST"])
@login_required
def bridge(src_table, src_id, tgt_table):
    data = request.get_json(silent=True) or {}
    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        return jsonify({"error": "overrides must be an object",
                        "request_id": _rid()}), 400
    idem = (data.get("idempotency_key") or "").strip()

    sess = get_session()
    if _sensitive_bridge(src_table, tgt_table) and not _is_admin():
        return jsonify({
            "error": "Bridge requires admin access",
            "request_id": _rid(),
        }), 403

    visible = _source_detail_visible(sess, src_table, src_id)
    if visible is False:
        return jsonify({"error": "Not found", "request_id": _rid()}), 404

    try:
        new_id, target = bridge_record(
            sess, src_table, src_id, tgt_table,
            overrides=overrides, idempotency_key=idem,
        )
    except BridgeError as e:
        return jsonify({"error": str(e),
                        "request_id": _rid()}), e.status_code
    sess.commit()
    return jsonify({
        "source": {"table": src_table, "id": src_id},
        "target_table": tgt_table,
        "target_id": new_id,
        "target": target,
        "idempotency_key": idem or None,
    }), 201
