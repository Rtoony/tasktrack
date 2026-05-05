"""Unified inbox endpoint — single capture surface for the Nexus suite.

Surface, all under /api/v1/inbox:
  POST   /                         token-scoped capture (any source)
  GET    /                         login: list inbox items (status filter)
  GET    /<id>                     login: single item
  PATCH  /<id>                     login: update fields
  POST   /<id>/promote             login: promote to a tracker
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

from datetime import datetime

from flask import Blueprint, Response, g, jsonify, request, session
from sqlalchemy import select

from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import InboxItem, to_dict
from ..services.audit import log_activity
from ..services.tickets import TABLE_MODELS, create_direct_record
from ..tokens import check_scoped_token

bp = Blueprint("inbox", __name__)


# ── POST /api/v1/inbox  (token-scoped capture) ───────────────────────────

@bp.route("/api/v1/inbox", methods=["POST"])
def capture():
    auth = check_scoped_token("inbox")
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
        created_by_name=source,  # display: who captured it
    )
    sess.add(item)
    sess.flush()
    log_activity(sess, "inbox_items", item.id, "captured",
                 new=f"{source}: {title[:80]}")
    sess.commit()
    sess.refresh(item)
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


# ── POST /api/v1/inbox/<id>/promote ──────────────────────────────────────

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
    payload = {"title": item.title}

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

    # Caller-supplied field overrides land last.
    overrides = data.get("overrides") or {}
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if k in cfg["fields"]:
                payload[k] = v

    record_id, error = create_direct_record(
        sess, target_table, payload, source_name=f"inbox-promote",
    )
    if error:
        return jsonify({"error": error}), 400

    item.promoted_to_table = target_table
    item.promoted_to_id = record_id
    item.status = "Archived"
    item.updated_at = datetime.now()
    log_activity(sess, "inbox_items", item.id, "promoted",
                 new=f"{target_table}#{record_id}")
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
