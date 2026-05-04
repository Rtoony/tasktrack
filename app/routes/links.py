"""Hyperlinks REST API.

Surface, all under /api/v1/links:
  GET    /<table>/<record_id>      list links on a record
  POST   /<table>/<record_id>      add a link {url, label?}
  DELETE /<id>                     remove a link
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request
from sqlalchemy import select

from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import Link, to_dict
from ..services import links as link_svc
from ..services.tickets import TABLE_MODELS

bp = Blueprint("links", __name__)


def _record_exists(sess, table: str, record_id: int) -> bool:
    Model = TABLE_MODELS.get(table)
    if Model is None:
        return False
    return sess.scalar(select(Model.id).where(Model.id == record_id)) is not None


@bp.route("/api/v1/links/<table>/<int:record_id>", methods=["GET"])
@login_required
def list_links(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    if not _record_exists(sess, table, record_id):
        return jsonify({"error": "Record not found"}), 404
    rows = link_svc.list_for(sess, table, record_id)
    return jsonify([to_dict(r) for r in rows])


@bp.route("/api/v1/links/<table>/<int:record_id>", methods=["POST"])
@login_required
def add_link(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    if not _record_exists(sess, table, record_id):
        return jsonify({"error": "Record not found"}), 404

    data = request.json or {}
    try:
        link = link_svc.add_link(
            sess, table, record_id,
            url=data.get("url", ""),
            label=data.get("label"),
        )
    except link_svc.LinkError as e:
        return jsonify({"error": str(e)}), e.status_code

    sess.commit()
    sess.refresh(link)
    return jsonify(to_dict(link)), 201


@bp.route("/api/v1/links/<int:link_id>", methods=["DELETE"])
@login_required
def delete_link(link_id):
    sess = get_session()
    try:
        link_svc.delete_link(sess, link_id)
    except link_svc.LinkError as e:
        return jsonify({"error": str(e)}), e.status_code
    sess.commit()
    return Response(status=204)
