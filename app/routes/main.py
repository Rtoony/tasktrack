"""Top-level dashboard + healthcheck."""
import json
import os
import struct
import subprocess
import zlib
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    render_template,
    session,
)

from ..auth import login_required

bp = Blueprint("main", __name__)

PWA_THEME_COLOR = "#0f62fe"
PWA_BACKGROUND_COLOR = "#f4f4f4"
PWA_ICON_SIZES = (192, 512)


@bp.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
        standalone_tab=None,
        standalone_title=None,
        standalone_subtitle=None,
    )


@bp.route("/capture/ocr")
@login_required
def capture_ocr():
    return render_template(
        "capture_ocr.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


@bp.route("/testing")
@login_required
def testing_checklist():
    return render_template(
        "testing_checklist.html",
        user_name=session.get("user_name", ""),
        user_role=session.get("user_role", "user"),
    )


def _git_value(args: list[str]) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return ""


@bp.route("/api/v1/app-context")
@login_required
def app_context():
    """Small diagnostic payload for feedback records.

    This intentionally excludes secrets, request bodies, and full paths.
    The goal is to pin a feedback item to the running app/build context.
    """
    dirty = bool(_git_value(["status", "--short"]))
    return jsonify({
        "app": "tasktrack",
        "brand": current_app.config.get("BRAND_NAME", "TaskTrack"),
        "server_time": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "request_id": g.get("request_id", ""),
        "git": {
            "commit": _git_value(["rev-parse", "HEAD"]),
            "short_commit": _git_value(["rev-parse", "--short", "HEAD"]),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "dirty": dirty,
        },
        "runtime": {
            "profile": current_app.config.get("PROFILE", ""),
            "db_name": Path(current_app.config.get("DB_PATH", "tracker.db")).name,
            "build_id": os.environ.get("TASKTRACK_BUILD_ID", ""),
        },
    })


@bp.route("/manifest.webmanifest")
def webmanifest():
    brand = current_app.config.get("BRAND_NAME", "TaskTrack")
    manifest = {
        "id": "/",
        "name": brand,
        "short_name": "TaskTrack",
        "description": "TaskTrack internal operations dashboard",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": PWA_BACKGROUND_COLOR,
        "theme_color": PWA_THEME_COLOR,
        "icons": [
            {
                "src": f"/pwa-icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any maskable",
            }
            for size in PWA_ICON_SIZES
        ],
    }
    response = Response(
        json.dumps(manifest, separators=(",", ":")),
        mimetype="application/manifest+json",
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/service-worker.js")
def service_worker():
    """Root-scoped service worker shell.

    Intentionally omits a fetch handler and CacheStorage use so API and
    HTML responses are never cached by the PWA layer.
    """
    source = """const SW_VERSION = "tasktrack-pwa-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
"""
    response = Response(source, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/pwa-icon-<int:size>.png")
def pwa_icon(size: int):
    if size not in PWA_ICON_SIZES:
        abort(404)
    response = Response(_pwa_icon_png(size), mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@lru_cache(maxsize=len(PWA_ICON_SIZES))
def _pwa_icon_png(size: int) -> bytes:
    """Generate a square PNG icon without adding image dependencies."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(_pwa_icon_pixel(size, x, y))

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png.extend(_png_chunk(b"IHDR", header))
    png.extend(_png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
    png.extend(_png_chunk(b"IEND", b""))
    return bytes(png)


def _pwa_icon_pixel(size: int, x: int, y: int) -> tuple[int, int, int, int]:
    fx = x / size
    fy = y / size

    if fx < 0.08 or fy > 0.88:
        return (15, 98, 254, 255)

    in_first_t = (
        (0.18 <= fx <= 0.47 and 0.20 <= fy <= 0.31)
        or (0.29 <= fx <= 0.36 and 0.20 <= fy <= 0.68)
    )
    in_second_t = (
        (0.53 <= fx <= 0.82 and 0.20 <= fy <= 0.31)
        or (0.64 <= fx <= 0.71 and 0.20 <= fy <= 0.68)
    )
    if in_first_t or in_second_t:
        return (255, 255, 255, 255)

    shade = int(22 + (fx * 18) + (fy * 12))
    return (shade, shade, shade, 255)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    body = chunk_type + data
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + body + struct.pack(">I", checksum)


@bp.route("/healthz")
def healthz():
    return "ok"
