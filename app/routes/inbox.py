"""Unified inbox endpoint — single capture surface for the Nexus suite.

Surface, all under /api/v1/inbox:
  POST   /                         token-scoped capture (any source)
  GET    /                         login: list inbox items (status filter)
  GET    /<id>                     login: single item
  PATCH  /<id>                     login: update fields
  POST   /<id>/suggest             login or inbox/triage token: run the
                                   classifier, store the ADVISORY suggestion
  POST   /<id>/promote             login: promote ("assign") to a tracker
  DELETE /<id>                     login: hard delete

POST is the unified write path. Body:
  {
    "title": "required, ≤256",
    "body": "optional",
    "source": "label of the writer (mytrack-bot, paperless, voice, ...)",
    "source_ref": "optional external id for dedupe",
    "target_table": "optional — if set + valid, lands in that tracker
                     instead of inbox_items",
    "priority": "Low|Medium|High",
    "due_date": "optional ISO date"
  }

If `target_table` is given, this skips the inbox entirely and creates
a record in the named tracker (uses the same create_direct_record
plumbing as the intake forms). Useful when the caller already knows
where the item belongs.

If `source_ref` is given AND a row with the same (source, source_ref)
already exists in inbox_items, the POST is a no-op and returns the
existing row. Lets bots safely retry.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

from flask import (
    Blueprint,
    Response,
    current_app,
    has_request_context,
    jsonify,
    request,
    session,
)
from sqlalchemy import select

from .. import limiter
from .. import profile as _profile
from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import ActivityLog, InboxItem, to_dict
from ..services import triage as triage_svc
from ..services.audit import log_activity
from ..services.tickets import create_direct_record
from ..services.triage import run_classify
from ..tokens import check_scoped_token

LOG = logging.getLogger("tasktrack.inbox")

bp = Blueprint("inbox", __name__)


def _token_post_limit():
    return f"{_profile.TOKEN_API_RATE_LIMIT_PER_HR_PER_IP} per hour"


# ── POST /api/v1/inbox  (session or token-scoped capture) ────────────────

def _require_capture_auth():
    """Allow browser capture through the login session plus token writers.

    Accepts the inbox scope (canonical) or the triage scope — the email
    intake poller holds the triage token (it also drives the attachments
    upload with it) and captures into the inbox without a second secret.
    Mirrors _require_suggest_auth below.
    """
    if "user_id" in session:
        return None
    inbox_err = check_scoped_token("inbox")
    if inbox_err is None:
        return None
    if check_scoped_token("triage") is None:
        return None
    return inbox_err


# ── Advisory suggestion core (Triage+Assignment unification) ─────────────
#
# The inbox is the dump ground; the SYSTEM suggests a target tracker +
# drafted fields (stored on the item, ADVISORY only); the HUMAN has final
# say at promote ("Assignment") time. Suggestions never auto-create
# tracker rows, and rows created at assignment time carry NO needs_review
# flag — a human just reviewed them.

# INTAKE_META fields (from the B&R form) worth forwarding to the
# classifier as operator hints. They steer the model only — run_classify
# never force-merges them into the drafted fields.
_HINT_FIELD_KEYS = (
    "project", "skill", "software", "who", "trainees",
    "goals", "category", "severity", "involved",
)


def _intake_hints(body):
    """Extract classifier hints from an INTAKE_META line in an item body.

    The B&R intake form embeds a JSON metadata line ("INTAKE_META: {...}")
    carrying the request type and raw form fields. Parsed defensively:
    any malformed payload returns None — hints only steer the model.
    """
    for line in (body or "").splitlines():
        line = line.strip()
        if not line.startswith("INTAKE_META:"):
            continue
        try:
            meta = json.loads(line[len("INTAKE_META:"):].strip())
        except (ValueError, TypeError):
            return None
        if not isinstance(meta, dict):
            return None
        hints = {}
        rtype = str(meta.get("type") or "").strip()
        if rtype:
            hints["request_type"] = rtype
        target = str(meta.get("suggested_target") or "").strip()
        if target and target != "triage":
            hints["requested_target"] = target
        meta_fields = meta.get("fields")
        if isinstance(meta_fields, dict):
            for key in _HINT_FIELD_KEYS:
                val = meta_fields.get(key)
                sval = str(val).strip() if val is not None else ""
                if sval:
                    hints[key] = sval
        return hints or None
    return None


def _log_suggested(sess, item_id, target):
    """Activity row for a suggestion write.

    log_activity reads flask.session, which doesn't exist on the
    background auto-suggest thread — write the row directly there.
    """
    if has_request_context():
        log_activity(sess, "inbox_items", item_id, "suggested", new=target)
        return
    sess.add(ActivityLog(
        table_name="inbox_items",
        record_id=item_id,
        action="suggested",
        field_name="",
        old_value="",
        new_value=str(target),
        user_name="System",
    ))


def run_suggest_for_item(sess, item):
    """Classify an inbox item and store the ADVISORY suggestion on it.

    Synchronous core shared by POST /<id>/suggest and the capture-time
    background refine. Raises RuntimeError (from run_classify) on model
    failure, leaving the item untouched. Re-runs overwrite the previous
    suggestion. Never creates tracker rows.
    """
    raw_text = item.title + (("\n\n" + item.body) if item.body else "")
    hints = _intake_hints(item.body)
    suggestion, _model = run_classify(raw_text, hints=hints)
    item.suggested_table = suggestion["target_table"]
    item.suggestion_json = json.dumps(suggestion)
    item.suggested_at = datetime.now()
    item.updated_at = datetime.now()
    _log_suggested(sess, item.id, suggestion["target_table"])
    return suggestion


def auto_suggest_enabled(app) -> bool:
    """Config gate for the capture-time background suggest.

    Off when INBOX_AUTO_SUGGEST (env, default ON) is disabled, and
    silently skipped when no triage model is configured at all.
    """
    if not app.config.get("INBOX_AUTO_SUGGEST", True):
        return False
    return bool(triage_svc.TRIAGE_MODEL_LOCAL or triage_svc.TRIAGE_MODEL_CLOUD)


def _auto_suggest_worker(app, item_id):
    """Background-thread body: own app context + own DB session.

    Best-effort by design — every failure is logged and swallowed so the
    thread can never disturb the request that spawned it.
    """
    try:
        with app.app_context():
            sess = get_session()
            item = sess.get(InboxItem, item_id)
            if item is None:
                return
            run_suggest_for_item(sess, item)
            sess.commit()
            LOG.info("auto-suggest stored for inbox item %s -> %s",
                     item_id, item.suggested_table)
    except Exception:  # noqa: BLE001 — best-effort background work
        LOG.exception("auto-suggest failed for inbox item %s", item_id)


def spawn_auto_suggest(item_id):
    """Fire-and-forget background suggestion for a just-captured item.

    Mirrors the health-probe daemon-thread pattern. Returns the Thread
    (or None when skipped) so ops tooling could join if it ever cares.
    Capture latency is unaffected — the caller has already committed.
    """
    # Skipped under pytest the same way create_app skips health probes —
    # tests exercise run_suggest_for_item / _auto_suggest_worker directly.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    app = current_app._get_current_object()
    if not auto_suggest_enabled(app):
        return None
    thread = threading.Thread(
        target=_auto_suggest_worker,
        args=(app, item_id),
        name=f"tasktrack-suggest-{item_id}",
        daemon=True,
    )
    thread.start()
    return thread


@bp.route("/api/v1/inbox", methods=["POST"])
@limiter.limit(_token_post_limit, methods=["POST"])
def capture():
    auth = _require_capture_auth()
    if auth is not None:
        return auth

    data = request.json or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if len(title) > 256:
        title = title[:256]

    body = (data.get("body") or "").strip()
    source = (data.get("source") or "manual").strip()[:64]
    source_ref = (data.get("source_ref") or "").strip()[:128]
    priority = (data.get("priority") or "Medium").strip()
    due_date = (data.get("due_date") or "").strip()
    target_table = (data.get("target_table") or "").strip()

    sess = get_session()

    # Direct-route into a tracker if the caller knows where it belongs.
    if target_table:
        if target_table not in ALLOWED_TABLES or target_table == "inbox_items":
            return jsonify({"error": f"unknown target_table: {target_table}"}), 400
        payload = {"title": title}
        # Common-fields best-effort mapping.
        if body and "description" in ALLOWED_TABLES[target_table]["fields"]:
            payload["description"] = body
        elif body and "task_description" in ALLOWED_TABLES[target_table]["fields"]:
            payload["task_description"] = body
        elif body and "notes" in ALLOWED_TABLES[target_table]["fields"]:
            payload["notes"] = body
        if priority and "priority" in ALLOWED_TABLES[target_table]["fields"]:
            payload["priority"] = priority
        if due_date:
            for due_field in ("due_date", "due_at", "follow_up_date"):
                if due_field in ALLOWED_TABLES[target_table]["fields"]:
                    payload[due_field] = due_date
                    break
        if "source" in ALLOWED_TABLES[target_table]["fields"]:
            payload["source"] = source

        record_id, error = create_direct_record(
            sess, target_table, payload, source_name=f"inbox:{source}",
        )
        if error:
            return jsonify({"error": error}), 400
        sess.commit()
        return jsonify({
            "routed_to": target_table,
            "record_id": record_id,
        }), 201

    # Dedupe by (source, source_ref) when the caller supplied a ref.
    if source_ref:
        existing = sess.scalar(
            select(InboxItem).where(
                InboxItem.source == source,
                InboxItem.source_ref == source_ref,
            )
        )
        if existing is not None:
            return jsonify(to_dict(existing)), 200

    item = InboxItem(
        title=title,
        body=body,
        source=source,
        source_ref=source_ref,
        priority=priority,
        due_date=due_date,
        created_by_user_id=session.get("user_id"),
        created_by_name=session.get("user_name") or source,  # display: who captured it
    )
    sess.add(item)
    sess.flush()
    log_activity(sess, "inbox_items", item.id, "captured",
                 new=f"{source}: {title[:80]}")
    sess.commit()
    sess.refresh(item)
    # Best-effort background classification (advisory suggestion). Never
    # blocks or fails the capture; gated by INBOX_AUTO_SUGGEST.
    spawn_auto_suggest(item.id)
    return jsonify(to_dict(item)), 201


# ── GET /api/v1/inbox (list) ─────────────────────────────────────────────

@bp.route("/api/v1/inbox", methods=["GET"])
@login_required
def list_items():
    sess = get_session()
    status = (request.args.get("status") or "").strip()
    q = select(InboxItem).order_by(InboxItem.created_at.desc())
    if status:
        q = q.where(InboxItem.status == status)
    else:
        # Default view hides Archived noise.
        q = q.where(InboxItem.status != "Archived")
    rows = sess.scalars(q.limit(500)).all()
    return jsonify([to_dict(r) for r in rows])


@bp.route("/api/v1/inbox/<int:item_id>", methods=["GET"])
@login_required
def get_item(item_id):
    sess = get_session()
    item = sess.get(InboxItem, item_id)
    if item is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(to_dict(item))


# ── PATCH /api/v1/inbox/<id> ─────────────────────────────────────────────

_PATCHABLE = {"title", "body", "status", "priority", "due_date"}


@bp.route("/api/v1/inbox/<int:item_id>", methods=["PATCH"])
@login_required
def patch_item(item_id):
    sess = get_session()
    item = sess.get(InboxItem, item_id)
    if item is None:
        return jsonify({"error": "Not found"}), 404
    data = request.json or {}
    changed = []
    for key, value in data.items():
        if key not in _PATCHABLE:
            continue
        old = getattr(item, key)
        new = (value or "") if isinstance(value, str) else value
        if old == new:
            continue
        setattr(item, key, new)
        changed.append((key, old, new))
    if not changed:
        return jsonify(to_dict(item))
    item.updated_at = datetime.now()
    if any(k == "status" and v == "Done" for k, _o, v in changed):
        item.completed_at = datetime.now()
    for key, old, new in changed:
        log_activity(sess, "inbox_items", item.id, "updated",
                     field=key, old=str(old), new=str(new))
    sess.commit()
    sess.refresh(item)
    return jsonify(to_dict(item))


# ── POST /api/v1/inbox/<id>/suggest ──────────────────────────────────────

def _require_suggest_auth():
    """Session, inbox-scoped token, or triage-scoped token.

    Same capture-auth pattern as the other inbox POSTs, widened so the
    email poller / triage clients (which hold the triage scope) can ask
    for suggestions too.
    """
    if "user_id" in session:
        return None
    inbox_err = check_scoped_token("inbox")
    if inbox_err is None:
        return None
    if check_scoped_token("triage") is None:
        return None
    return inbox_err


@bp.route("/api/v1/inbox/<int:item_id>/suggest", methods=["POST"])
@limiter.limit(_token_post_limit, methods=["POST"])
def suggest(item_id):
    """Run the classifier and store the ADVISORY suggestion on the item.

    Idempotent — re-runs overwrite the stored suggestion. On model
    failure returns 502 with detail and leaves the item untouched.
    Never creates tracker records.
    """
    auth = _require_suggest_auth()
    if auth is not None:
        return auth

    sess = get_session()
    item = sess.get(InboxItem, item_id)
    if item is None:
        return jsonify({"error": "Not found"}), 404

    try:
        suggestion = run_suggest_for_item(sess, item)
    except RuntimeError as exc:
        sess.rollback()
        return jsonify({"error": "suggest failed", "detail": str(exc)}), 502

    sess.commit()
    sess.refresh(item)
    return jsonify({
        "suggestion": suggestion,
        "inbox_item": to_dict(item),
    })


# ── POST /api/v1/inbox/<id>/promote  (Assignment) ────────────────────────

@bp.route("/api/v1/inbox/<int:item_id>/promote", methods=["POST"])
@login_required
def promote(item_id):
    sess = get_session()
    item = sess.get(InboxItem, item_id)
    if item is None:
        return jsonify({"error": "Not found"}), 404

    data = request.json or {}
    target_table = (data.get("target_table") or "").strip()
    if not target_table or target_table not in ALLOWED_TABLES or target_table == "inbox_items":
        return jsonify({"error": f"unknown target_table: {target_table}"}), 400

    cfg = ALLOWED_TABLES[target_table]
    payload = {}
    if "title" in cfg["fields"]:
        payload["title"] = item.title

    # Carry body into whichever long-text field the target tracker has.
    if item.body:
        for body_field in ("body", "description", "task_description",
                           "issue_description", "training_goals", "notes"):
            if body_field in cfg["fields"]:
                payload[body_field] = item.body
                break

    if item.priority and "priority" in cfg["fields"]:
        payload["priority"] = item.priority
    if item.due_date:
        for due_field in ("due_date", "due_at", "follow_up_date"):
            if due_field in cfg["fields"]:
                payload[due_field] = item.due_date
                break
    if "source" in cfg["fields"]:
        payload["source"] = f"inbox:{item.source}"

    # Caller-supplied field overrides land last (the assignment modal
    # sends the full reviewed field set here).
    overrides = data.get("overrides") or {}
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if k in cfg["fields"]:
                payload[k] = v

    # A human is reviewing right now — assignment rows NEVER carry the
    # needs_review flag, regardless of what the client sent.
    payload.pop("needs_review", None)

    # Target-specific conveniences so bare promotes still validate when
    # the inbox item itself carries enough signal.
    if ("issue_description" in cfg["fields"]
            and not str(payload.get("issue_description") or "").strip()):
        payload["issue_description"] = item.body or item.title
    if ("severity" in cfg["fields"]
            and not str(payload.get("severity") or "").strip()
            and item.priority in ("Low", "Medium", "High")):
        payload["severity"] = item.priority

    # Structured required-field validation: tell the assignment UI
    # exactly which fields are still missing.
    missing = [req for req in cfg["required"]
               if not str(payload.get(req) or "").strip()]
    if missing:
        return jsonify({
            "error": "missing required fields",
            "missing": missing,
        }), 400

    record_id, error = create_direct_record(
        sess, target_table, payload, source_name="inbox-promote",
    )
    if error:
        return jsonify({"error": error}), 400

    item.promoted_to_table = target_table
    item.promoted_to_id = record_id
    item.status = "Archived"
    item.updated_at = datetime.now()
    # Suggestion columns stay as-is — they're the history of what the AI
    # proposed. Note disagreement in the activity log when it happened.
    suggested = (item.suggested_table or "").strip()
    if suggested and suggested != target_table:
        detail = f"assigned to {target_table}#{record_id} (AI suggested {suggested})"
    else:
        detail = f"{target_table}#{record_id}"
    log_activity(sess, "inbox_items", item.id, "promoted", new=detail)
    sess.commit()
    sess.refresh(item)
    return jsonify({
        "inbox_item": to_dict(item),
        "promoted_to": {"table": target_table, "id": record_id},
    }), 201


# ── DELETE /api/v1/inbox/<id> ────────────────────────────────────────────

@bp.route("/api/v1/inbox/<int:item_id>", methods=["DELETE"])
@login_required
def delete_item(item_id):
    sess = get_session()
    item = sess.get(InboxItem, item_id)
    if item is None:
        return jsonify({"error": "Not found"}), 404
    log_activity(sess, "inbox_items", item.id, "deleted", old=item.title[:80])
    sess.delete(item)
    sess.commit()
    return Response(status=204)
