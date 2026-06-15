"""Saved Views (WORK_PLAN P1-5).

Per-user, named filter/sort configurations for the interactive tracker
tabs. The frontend captures its live filter-bar state into an opaque JSON
blob; this module stores, lists, and removes those blobs scoped to the
logged-in user. It deliberately does NOT interpret the state — that keeps
the API stable as each tab's filter set evolves — but it does validate the
envelope (name, tab, well-formed JSON, size) so junk can't accumulate.

Distinct from the report presets in ``reports.py``: those target the
read-only management report surfaces and support sharing; saved views are
strictly owner-scoped.
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request, session
from sqlalchemy import select

from ..auth import login_required
from ..db import get_session
from ..models import SavedView

bp = Blueprint("saved_views", __name__)

# Tabs that own an interactive filter-bar a view can be saved against.
# Mirrors API_MAP / sortState in templates/index.html. Kept server-side so a
# malformed or stale client can't create views for surfaces that have no
# filter bar to re-apply them to.
SAVED_VIEW_TABS = {
    "work", "project", "training", "personnel",
    "triage", "calendar", "personal",
}

MAX_NAME_LEN = 80
# Filter state is small (a handful of control values + sort); cap well above
# any realistic payload but low enough to reject abuse.
MAX_STATE_BYTES = 8000


def _to_dict(row: SavedView, *, include_state: bool = True) -> dict:
    out = {
        "id": row.id,
        "name": row.name,
        "tab": row.tab,
        "created_at": str(row.created_at or ""),
        "updated_at": str(row.updated_at or ""),
    }
    if include_state:
        try:
            out["state"] = json.loads(row.state_json or "{}")
        except (TypeError, ValueError):
            out["state"] = {}
    return out


def _serialize_payload(data: dict) -> tuple[dict | None, str | None]:
    """Validate the request body into column values, or return an error."""
    name = str(data.get("name") or "").strip()
    if not name:
        return None, "name is required"
    if len(name) > MAX_NAME_LEN:
        return None, f"name must be {MAX_NAME_LEN} characters or fewer"

    tab = str(data.get("tab") or "").strip()
    if tab not in SAVED_VIEW_TABS:
        return None, "unsupported tab"

    state = data.get("state")
    if state is None or not isinstance(state, dict):
        return None, "state must be an object"
    state_json = json.dumps(state, separators=(",", ":"))
    if len(state_json.encode("utf-8")) > MAX_STATE_BYTES:
        return None, "state is too large"

    return {"name": name, "tab": tab, "state_json": state_json}, None


@bp.route("/api/v1/saved-views", methods=["GET"])
@login_required
def saved_views_list():
    tab = (request.args.get("tab") or "").strip()
    user_id = session.get("user_id")
    stmt = select(SavedView).where(SavedView.owner_user_id == user_id)
    if tab:
        if tab not in SAVED_VIEW_TABS:
            return jsonify({"error": "unsupported tab"}), 400
        stmt = stmt.where(SavedView.tab == tab)
    stmt = stmt.order_by(SavedView.name.asc(), SavedView.id.asc())
    rows = get_session().scalars(stmt).all()
    return jsonify({"views": [_to_dict(row) for row in rows]})


@bp.route("/api/v1/saved-views", methods=["POST"])
@login_required
def saved_views_create():
    data = request.get_json(silent=True) or {}
    payload, error = _serialize_payload(data)
    if error:
        return jsonify({"error": error}), 400

    sess = get_session()
    user_id = session.get("user_id")
    # Re-saving under an existing name for the same tab overwrites it, so the
    # operator can iterate on "My overdue" without piling up duplicates.
    existing = sess.scalars(
        select(SavedView).where(
            SavedView.owner_user_id == user_id,
            SavedView.tab == payload["tab"],
            SavedView.name == payload["name"],
        )
    ).first()
    if existing is not None:
        existing.state_json = payload["state_json"]
        sess.commit()
        return jsonify(_to_dict(existing)), 200

    row = SavedView(
        name=payload["name"],
        tab=payload["tab"],
        state_json=payload["state_json"],
        owner_user_id=user_id,
    )
    sess.add(row)
    sess.commit()
    return jsonify(_to_dict(row)), 201


@bp.route("/api/v1/saved-views/<int:view_id>", methods=["DELETE"])
@login_required
def saved_views_delete(view_id: int):
    sess = get_session()
    row = sess.get(SavedView, view_id)
    # Owner-scoped: a view that isn't yours is indistinguishable from one that
    # doesn't exist, so neither path leaks another user's saved views.
    if row is None or row.owner_user_id != session.get("user_id"):
        return jsonify({"error": "not found"}), 404
    sess.delete(row)
    sess.commit()
    return jsonify({"ok": True})
