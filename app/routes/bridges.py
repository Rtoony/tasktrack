"""Cross-tracker bridge endpoints (Phase 3).

- GET  /api/v1/bridge/<src_table>/targets   — list allowed targets
                                              + their required overrides
- POST /api/v1/bridge/<src_table>/<src_id>/<tgt_table>
       body: { "overrides": {...}, "idempotency_key": "..." }

Both endpoints require a logged-in user. Mutations don't require admin
because promoting a record only creates a new tracker row of the same
permission shape — anyone who can already write to the target table
can bridge into it.
"""
from flask import Blueprint, g, jsonify, request

from ..auth import login_required
from ..db import get_session
from ..services.bridges import BridgeError, bridge_record, get_targets_for

bp = Blueprint("bridges", __name__)


def _rid():
    return g.get("request_id", "-")


@bp.route("/api/v1/bridge/<src_table>/targets", methods=["GET"])
@login_required
def list_bridge_targets(src_table):
    """Return the allowed (target, label, required_overrides) triples
    for a given source table. Used to populate the UI's promote dropdown."""
    return jsonify(get_targets_for(src_table))


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
