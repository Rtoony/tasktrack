#!/usr/bin/env python3
"""TaskTrack Telegram bot worker.

The bot is a regular HTTP client of TaskTrack — no `from app import …`,
no direct sqlite3 access. Pairing, activity tracking, and ticket
creation all flow through /api/v1/telegram/* with the bot-scoped
TASKTRACK_TOKEN_BOT (legacy TASKTRACK_TOKEN still works during
the transition; server logs a deprecation each time it's used).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta

import requests


LOG = logging.getLogger("tasktrack.telegram_bot")
logging.basicConfig(
    level=os.environ.get("TASKTRACK_TELEGRAM_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)


# Telegram Bot API token — for talking to api.telegram.org. Distinct
# from the TaskTrack scoped bot token below.
BOT_TOKEN = (
    os.environ.get("TASKTRACK_TELEGRAM_TOKEN")
    or os.environ.get("MYTRACK_TELEGRAM_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
)
if not BOT_TOKEN:
    raise SystemExit("Telegram bot token not found in environment")

# TaskTrack API base + bot-scoped token — for talking to /api/v1/telegram/*.
TASKTRACK_API = os.environ.get("TASKTRACK_API_BASE", "http://127.0.0.1:5050").rstrip("/")
TASKTRACK_BOT_TOKEN = (
    os.environ.get("TASKTRACK_TOKEN_BOT")
    or os.environ.get("TASKTRACK_TOKEN")  # legacy; server logs deprecation
)
if not TASKTRACK_BOT_TOKEN:
    raise SystemExit("TASKTRACK_TOKEN_BOT (or legacy TASKTRACK_TOKEN) required")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_TIMEOUT = 45
REQUEST_TIMEOUT = 60
SESSIONS: dict[int, dict] = {}


def tasktrack_call(method: str, path: str, json_body: dict | None = None) -> dict:
    """Call /api/v1/telegram/* with the bot-scoped token. Returns {status, body}."""
    url = f"{TASKTRACK_API}{path}"
    headers = {"X-Token": TASKTRACK_BOT_TOKEN}
    try:
        response = requests.request(method, url, json=json_body, headers=headers, timeout=30)
    except requests.RequestException as exc:
        LOG.error("TaskTrack %s %s failed: %s", method, path, exc)
        return {"status": 0, "body": {"error": str(exc)}}
    try:
        data = response.json()
    except ValueError:
        data = {"error": f"non-json {response.status_code}"}
    if response.status_code >= 400:
        LOG.warning("TaskTrack %s %s -> %s %s", method, path, response.status_code, data)
    return {"status": response.status_code, "body": data}


GUIDED_FLOWS = {
    "work_tasks": {
        "label": "CAD Development",
        "fields": [
            {"key": "title", "prompt": "Task title?", "required": True},
            {"key": "cad_skill_area", "prompt": "CAD skill area? Send /skip if not needed.", "required": False},
            {"key": "description", "prompt": "Requested change?", "required": True},
            {"key": "requested_by", "prompt": "Who requested it? Send /skip to use your Telegram name.", "required": False},
            {"key": "due_date", "prompt": "Due date? Use YYYY-MM-DD, `today`, `tomorrow`, or /skip.", "required": False, "parser": "date"},
        ],
    },
    "project_work_tasks": {
        "label": "Project Work",
        "fields": [
            {"key": "project_number", "prompt": "Project number? Use ####.##", "required": True},
            {"key": "project_name", "prompt": "Project name?", "required": True},
            {"key": "title", "prompt": "Task title?", "required": True},
            {"key": "task_description", "prompt": "Task description?", "required": True},
            {"key": "billing_phase", "prompt": "Billing phase? Use ## or /skip.", "required": False},
            {"key": "engineer", "prompt": "Engineer? Send /skip if unknown.", "required": False},
            {"key": "due_at", "prompt": "Due date/time? Use YYYY-MM-DD HH:MM, `today HH:MM`, `tomorrow HH:MM`, or /skip.", "required": False, "parser": "datetime"},
        ],
    },
    "training_tasks": {
        "label": "Training",
        "fields": [
            {"key": "title", "prompt": "Training title?", "required": True},
            {"key": "trainees", "prompt": "Who needs it? Send /skip if not decided.", "required": False},
            {"key": "skill_area", "prompt": "Skill area? Send /skip if not needed.", "required": False},
            {"key": "training_goals", "prompt": "Training goals?", "required": True},
            {"key": "requested_by", "prompt": "Who requested it? Send /skip to use your Telegram name.", "required": False},
            {"key": "due_date", "prompt": "Target date? Use YYYY-MM-DD, `today`, `tomorrow`, or /skip.", "required": False, "parser": "date"},
        ],
    },
    "personnel_issues": {
        "label": "Capability Tracking",
        "fields": [
            {"key": "person_name", "prompt": "Staff member name?", "required": True},
            {"key": "cad_skill_area", "prompt": "CAD skill area? Send /skip if not needed.", "required": False},
            {"key": "issue_description", "prompt": "Observed gap or incident summary?", "required": True},
            {"key": "recommended_training", "prompt": "Recommended follow-up or training? Send /skip if none yet.", "required": False},
            {"key": "observed_by", "prompt": "Observed by? Send /skip to use your Telegram name.", "required": False},
        ],
    },
    "suggestion_box": {
        "label": "Suggestion Box",
        "fields": [
            {"key": "title", "prompt": "Suggestion title?", "required": True},
            {"key": "suggestion_type", "prompt": "Suggestion type? Examples: Training Idea, Template, Automation, Standards.", "required": False},
            {"key": "summary", "prompt": "Suggestion summary?", "required": True},
            {"key": "expected_value", "prompt": "Why would this help? Send /skip if you want to keep it brief.", "required": False},
            {"key": "submitted_for", "prompt": "Who should review it? Examples: Management, CAD Team, Myself. Send /skip for Management.", "required": False},
        ],
    },
}

CATEGORY_BUTTONS = [
    [("Project Work", "cat:project_work_tasks"), ("CAD Development", "cat:work_tasks")],
    [("Training", "cat:training_tasks"), ("Capability Tracking", "cat:personnel_issues")],
    [("Suggestion Box", "cat:suggestion_box")],
]


def telegram_api(method: str, payload: dict | None = None, timeout: int = REQUEST_TIMEOUT):
    response = requests.post(f"{API_BASE}/{method}", json=payload or {}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {data}")
    return data["result"]


def parse_allowed_chat_ids():
    raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS") or os.environ.get("MYTRACK_CHAT_ID") or ""
    values = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError:
            LOG.warning("Ignoring non-numeric TELEGRAM_ALLOWED_CHAT_IDS entry")
    return values


ENV_ALLOWED_CHAT_IDS = parse_allowed_chat_ids()


def is_authorized(chat_id: int) -> bool:
    if chat_id in ENV_ALLOWED_CHAT_IDS:
        return True
    res = tasktrack_call("POST", "/api/v1/telegram/touch", {"chat_id": chat_id})
    return bool(res.get("body", {}).get("paired"))


def pair_chat(user: dict, chat: dict, code: str) -> bool:
    """Pair a chat to TaskTrack using the global link code. Returns True on success."""
    res = tasktrack_call("POST", "/api/v1/telegram/pair", {
        "chat_id": chat["id"],
        "code": code.strip().upper(),
        "username": user.get("username", "") or "",
        "display_name": display_name_for_user(user),
    })
    return res.get("status") == 200


def touch_chat(chat_id: int, user: dict) -> None:
    tasktrack_call("POST", "/api/v1/telegram/touch", {
        "chat_id": chat_id,
        "username": user.get("username", "") or "",
        "display_name": display_name_for_user(user),
    })


def display_name_for_user(user: dict):
    full = " ".join(p for p in [user.get("first_name", ""), user.get("last_name", "")] if p).strip()
    return full or user.get("username") or f"Telegram {user.get('id')}"


def send_message(chat_id: int, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_api("sendMessage", payload)


def answer_callback(callback_id: str):
    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})


def main_menu_markup():
    return {
        "keyboard": [
            [{"text": "New Task"}, {"text": "Smart Capture"}],
            [{"text": "Quick CAD"}, {"text": "Quick Suggestion"}],
            [{"text": "Help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


# ── Smart Capture (Ollama-powered NL extraction) ────────────────────

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("TASKTRACK_NL_MODEL", "ministral-3:14b")

_NL_PROMPT_TEMPLATE = """You convert short Telegram messages into structured TaskTrack records.

Today's date: {today}

Output ONLY a JSON object matching this schema (no prose, no code fences):
{{
  "category": one of ["work_tasks", "project_work_tasks", "training_tasks", "personnel_issues", "suggestion_box"],
  "title": short title (under 60 chars),
  "description": fuller description (use the user's words),
  "due_date": "YYYY-MM-DD" or null,
  "project_number": string like "1234.56" or null (only for project_work_tasks),
  "person_name": string or null (only for personnel_issues),
  "confidence": float 0.0-1.0
}}

Category cues:
- work_tasks       → CAD / drafting / drawing / standards / template
- project_work_tasks → has a project number ####.##  or names a project
- training_tasks   → training / teach / show / how-to / onboarding
- personnel_issues → someone's skill gap / mistake / capability concern
- suggestion_box   → idea / proposal / "what if" / improvement
If unsure, pick the most plausible and lower confidence.

User message:
\"\"\"{text}\"\"\"
"""


def extract_task_from_text(text: str) -> dict | None:
    """Call local Ollama to parse free-form text into a structured record.
    Returns None if Ollama is unreachable or output is unparseable."""
    today = date.today().isoformat()
    prompt = _NL_PROMPT_TEMPLATE.format(today=today, text=text.replace('"""', '"​""'))
    body = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=30)
        r.raise_for_status()
        raw = r.json().get("response") or ""
    except (requests.RequestException, ValueError) as exc:
        LOG.warning("ollama generate failed: %s", exc)
        return None
    try:
        import json as _json
        parsed = _json.loads(raw)
    except (ValueError, TypeError):
        LOG.warning("ollama returned non-JSON: %s", raw[:200])
        return None
    if not isinstance(parsed, dict):
        return None
    cat = parsed.get("category")
    if cat not in GUIDED_FLOWS:
        return None
    title = (parsed.get("title") or "").strip()
    if not title:
        return None
    # Sanitize due_date: only keep if it parses as YYYY-MM-DD
    raw_due = parsed.get("due_date")
    if raw_due:
        try:
            parsed["due_date"] = parse_date_input(str(raw_due))
        except (ValueError, TypeError):
            parsed["due_date"] = None
    return parsed


def smart_capture_preview(parsed: dict) -> str:
    cat = parsed["category"]
    label = GUIDED_FLOWS[cat]["label"]
    lines = [
        f"🤖 *Smart Capture preview*",
        f"Category: *{label}* (`{cat}`)",
        f"Title: _{parsed.get('title','')}_",
    ]
    if parsed.get("description"):
        desc = parsed["description"]
        if len(desc) > 240:
            desc = desc[:237] + "..."
        lines.append(f"Description: {desc}")
    if parsed.get("due_date"):
        lines.append(f"Due: `{parsed['due_date']}`")
    if parsed.get("project_number"):
        lines.append(f"Project: `{parsed['project_number']}`")
    if parsed.get("person_name"):
        lines.append(f"Person: {parsed['person_name']}")
    conf = parsed.get("confidence")
    if isinstance(conf, (int, float)):
        lines.append(f"Confidence: `{conf:.2f}`")
    return "\n".join(lines)


def smart_capture_buttons() -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Save", "callback_data": "smart:save"},
            {"text": "❌ Cancel", "callback_data": "smart:cancel"},
            {"text": "Manual", "callback_data": "smart:manual"},
        ]]
    }


def smart_payload_to_record(parsed: dict, actor_name: str) -> tuple[str, dict]:
    """Translate Smart Capture JSON into the (table_name, payload) shape
    create_record() expects."""
    cat = parsed["category"]
    payload: dict = {
        "title": (parsed.get("title") or "")[:120],
        "description": parsed.get("description") or "",
        "created_by_name": f"Telegram (smart): {actor_name}",
    }
    if cat == "project_work_tasks":
        payload["project_number"] = parsed.get("project_number") or ""
        payload["project_name"] = parsed.get("project_name") or parsed.get("title") or ""
        payload["task_description"] = parsed.get("description") or ""
        payload.pop("description", None)
        if parsed.get("due_date"):
            payload["due_at"] = f"{parsed['due_date']} 17:00"
    elif cat == "personnel_issues":
        payload["person_name"] = parsed.get("person_name") or "Unknown"
        payload["issue_description"] = parsed.get("description") or parsed.get("title") or ""
        payload.pop("description", None)
        payload.pop("title", None)
    elif cat == "training_tasks":
        payload["training_goals"] = parsed.get("description") or parsed.get("title") or ""
        if parsed.get("due_date"):
            payload["due_date"] = parsed["due_date"]
    elif cat == "suggestion_box":
        payload["summary"] = parsed.get("description") or parsed.get("title") or ""
        payload.pop("description", None)
    elif cat == "work_tasks":
        if parsed.get("due_date"):
            payload["due_date"] = parsed["due_date"]
    return cat, payload


def category_markup():
    return {"inline_keyboard": [[{"text": text, "callback_data": data} for text, data in row] for row in CATEGORY_BUTTONS]}


def summarize_title(text: str, fallback: str):
    clean = " ".join((text or "").strip().split())
    if not clean:
        return fallback
    sentence = clean.split(".")[0].split("\n")[0].strip()
    return (sentence[:78] + "..") if len(sentence) > 80 else sentence


def parse_date_input(value: str):
    raw = value.strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered == "today":
        return date.today().isoformat()
    if lowered == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()


def parse_datetime_input(value: str):
    raw = value.strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("today "):
        clock = raw.split(" ", 1)[1]
        return f"{date.today().isoformat()}T{datetime.strptime(clock, '%H:%M').strftime('%H:%M')}"
    if lowered.startswith("tomorrow "):
        clock = raw.split(" ", 1)[1]
        return f"{(date.today() + timedelta(days=1)).isoformat()}T{datetime.strptime(clock, '%H:%M').strftime('%H:%M')}"
    if "T" in raw:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%dT%H:%M")
    return datetime.strptime(raw, "%Y-%m-%d %H:%M").strftime("%Y-%m-%dT%H:%M")


def build_payload_for_quick_mode(table_name: str, text: str, actor_name: str):
    if table_name == "work_tasks":
        return {
            "title": summarize_title(text, "Quick CAD capture"),
            "cad_skill_area": "",
            "description": text.strip(),
            "requested_by": actor_name,
            "request_reference": "Captured from Telegram quick entry.",
            "priority": "Medium",
            "status": "Not Started",
            "due_date": "",
            "notes": "Created from Telegram quick capture.",
            "created_by_user_id": None,
            "created_by_name": f"Telegram: {actor_name}",
        }
    if table_name == "suggestion_box":
        return {
            "title": summarize_title(text, "Quick suggestion"),
            "suggestion_type": "Other",
            "submitted_by": actor_name,
            "submitted_for": "Management",
            "summary": text.strip(),
            "expected_value": "",
            "priority": "Medium",
            "status": "New",
            "review_notes": "Created from Telegram quick suggestion.",
            "promoted_work_task_id": None,
            "created_by_user_id": None,
            "created_by_name": f"Telegram: {actor_name}",
        }
    raise ValueError("Unsupported quick mode")


def create_record(table_name: str, payload: dict, actor_name: str, chat_id: int):
    """Create a ticket via /api/v1/telegram/tickets. Server attributes the row.

    `actor_name` is unused on the bot side now (the server resolves the
    creator from telegram_chat_access.user_id or falls back to the chat
    display name). It's kept in the signature so call sites don't have
    to change.
    """
    res = tasktrack_call("POST", "/api/v1/telegram/tickets", {
        "chat_id": chat_id,
        "table": table_name,
        "payload": dict(payload),
    })
    body = res.get("body") or {}
    if res.get("status") in (200, 201):
        return body.get("task_id"), None
    return None, body.get("error") or f"server error {res.get('status')}"


def start_guided_flow(chat_id: int, table_name: str):
    SESSIONS[chat_id] = {"mode": "guided", "table": table_name, "step": 0, "data": {}}
    flow = GUIDED_FLOWS[table_name]
    send_message(chat_id, f"{flow['label']} selected.\n\n{flow['fields'][0]['prompt']}", main_menu_markup())


def prompt_next_step(chat_id: int):
    session = SESSIONS.get(chat_id)
    if not session:
        return
    flow = GUIDED_FLOWS[session["table"]]
    step = session["step"]
    if step >= len(flow["fields"]):
        finalize_guided_flow(chat_id)
        return
    send_message(chat_id, flow["fields"][step]["prompt"], main_menu_markup())


def finalize_guided_flow(chat_id: int):
    session = SESSIONS.get(chat_id)
    if not session:
        return
    table_name = session["table"]
    actor_name = session["actor_name"]
    data = dict(session["data"])

    if table_name == "work_tasks" and not data.get("requested_by"):
        data["requested_by"] = actor_name
    if table_name == "training_tasks" and not data.get("requested_by"):
        data["requested_by"] = actor_name
    if table_name == "personnel_issues" and not data.get("observed_by"):
        data["observed_by"] = actor_name
    if table_name == "suggestion_box":
        data.setdefault("submitted_by", actor_name)
        data.setdefault("submitted_for", "Management")

    if table_name in ("work_tasks", "project_work_tasks", "training_tasks"):
        data.setdefault("priority", "Medium")
    if table_name == "personnel_issues":
        data.setdefault("severity", "Medium")
    if "status" not in data or not data["status"]:
        data["status"] = ALLOWED_TABLES[table_name]["status_flow"][0]
    data["created_by_user_id"] = None
    data["created_by_name"] = f"Telegram: {actor_name}"
    note_lines = [f"Created from Telegram bot by {actor_name}."]
    if table_name == "work_tasks":
        data.setdefault("notes", "")
        data["notes"] = ("\n".join([data["notes"]] + note_lines).strip()) if data["notes"] else note_lines[0]
    elif table_name == "project_work_tasks":
        data.setdefault("notes", "")
        data["notes"] = ("\n".join([data["notes"]] + note_lines).strip()) if data["notes"] else note_lines[0]
    elif table_name == "training_tasks":
        data.setdefault("notes", "")
        data["notes"] = ("\n".join([data["notes"]] + note_lines).strip()) if data["notes"] else note_lines[0]
    elif table_name == "personnel_issues":
        data.setdefault("resolution_notes", "")
    elif table_name == "suggestion_box":
        data.setdefault("review_notes", "Created from Telegram bot.")

    record_id, error = create_record(table_name, data, actor_name, chat_id)
    if error:
        send_message(chat_id, f"I couldn't save that yet: {error}\n\nSend /menu to start over.", main_menu_markup())
    else:
        send_message(
            chat_id,
            f"Saved to {GUIDED_FLOWS[table_name]['label']} as record #{record_id}.",
            main_menu_markup(),
        )
    SESSIONS.pop(chat_id, None)


def handle_guided_input(chat_id: int, user: dict, text: str):
    session = SESSIONS.get(chat_id)
    if not session:
        return False
    if session["mode"] != "guided":
        return False

    flow = GUIDED_FLOWS[session["table"]]
    field = flow["fields"][session["step"]]
    raw = text.strip()
    if raw.lower() == "/skip" and not field["required"]:
        value = ""
    else:
        value = raw
        try:
            if field.get("parser") == "date" and value:
                value = parse_date_input(value)
            elif field.get("parser") == "datetime" and value:
                value = parse_datetime_input(value)
        except ValueError:
            send_message(chat_id, "That date format didn't parse. Try again or send /skip.", main_menu_markup())
            return True

    session["data"][field["key"]] = value
    session["actor_name"] = display_name_for_user(user)
    session["step"] += 1
    prompt_next_step(chat_id)
    return True


def begin_quick_mode(chat_id: int, table_name: str):
    label = "CAD Development" if table_name == "work_tasks" else "Suggestion Box"
    SESSIONS[chat_id] = {"mode": "quick", "table": table_name}
    send_message(
        chat_id,
        f"{label} quick capture is ready.\n\nSend one message and I'll save it. Use /cancel to abort.",
        main_menu_markup(),
    )


def handle_quick_input(chat_id: int, user: dict, text: str):
    session = SESSIONS.get(chat_id)
    if not session or session["mode"] != "quick":
        return False
    actor_name = display_name_for_user(user)
    payload = build_payload_for_quick_mode(session["table"], text, actor_name)
    record_id, error = create_record(session["table"], payload, actor_name, chat_id)
    if error:
        send_message(chat_id, f"I couldn't save that quick capture: {error}", main_menu_markup())
    else:
        label = "CAD Development" if session["table"] == "work_tasks" else "Suggestion Box"
        send_message(chat_id, f"Saved quick entry to {label} as record #{record_id}.", main_menu_markup())
    SESSIONS.pop(chat_id, None)
    return True


def handle_text_message(message: dict):
    chat = message["chat"]
    user = message.get("from", {})
    chat_id = chat["id"]
    text = (message.get("text") or "").strip()

    if not text:
        send_message(chat_id, "Text messages work best for TaskTrack entry right now.", main_menu_markup())
        return

    if text.lower().startswith("/link "):
        code = text.split(" ", 1)[1].strip()
        if code and pair_chat(user, chat, code):
            send_message(chat_id, "This chat is now linked to TaskTrack. Use the buttons below.", main_menu_markup())
        else:
            send_message(chat_id, "That pairing code did not match. Check the admin page and try again.")
        return

    authorized = is_authorized(chat_id)
    if not authorized:
        send_message(chat_id, "This chat is not paired yet. In TaskTrack admin, copy the Telegram pairing code and send `/link CODE` here.", {"remove_keyboard": True})
        return

    touch_chat(chat_id, user)

    if text.lower() in ("/start", "/menu"):
        send_message(chat_id, "TaskTrack bot ready.\n\nUse `New Task` for guided entry or the quick buttons for fast capture.", main_menu_markup())
        return
    if text.lower() == "/cancel":
        SESSIONS.pop(chat_id, None)
        send_message(chat_id, "Canceled the current draft.", main_menu_markup())
        return
    if text.lower() in ("/help", "help"):
        send_message(
            chat_id,
            "Commands:\n/start or /menu\n/cancel\n\nButtons:\nNew Task: guided workflow entry\nQuick CAD: fast CAD capture\nQuick Suggestion: fast idea capture",
            main_menu_markup(),
        )
        return
    if text == "New Task":
        send_message(chat_id, "Choose a category.", category_markup())
        return
    if text == "Quick CAD":
        begin_quick_mode(chat_id, "work_tasks")
        return
    if text == "Quick Suggestion":
        begin_quick_mode(chat_id, "suggestion_box")
        return
    if text in ("Smart Capture", "/smart"):
        SESSIONS[chat_id] = {"mode": "smart_pending"}
        send_message(
            chat_id,
            "🤖 Smart Capture ready.\n\nSend one paragraph describing your task. "
            "I'll extract the category, title, and due date. Use /cancel to abort.",
            main_menu_markup(),
        )
        return

    if handle_guided_input(chat_id, user, text):
        return
    if handle_quick_input(chat_id, user, text):
        return
    if handle_smart_input(chat_id, user, text):
        return

    send_message(chat_id, "Use `New Task`, `Smart Capture`, `Quick CAD`, or `Quick Suggestion` to start.", main_menu_markup())


def handle_smart_input(chat_id: int, user: dict, text: str) -> bool:
    session = SESSIONS.get(chat_id)
    if not session or session.get("mode") != "smart_pending":
        return False
    actor_name = display_name_for_user(user)
    parsed = extract_task_from_text(text)
    if not parsed:
        send_message(
            chat_id,
            "I couldn't parse that. Try the menu buttons, or rephrase with a clearer category cue (CAD, project ####.##, training, suggestion).",
            main_menu_markup(),
        )
        SESSIONS.pop(chat_id, None)
        return True
    SESSIONS[chat_id] = {
        "mode": "smart_confirm",
        "parsed": parsed,
        "actor_name": actor_name,
        "raw_text": text,
    }
    send_message(chat_id, smart_capture_preview(parsed), smart_capture_buttons())
    return True


def smart_capture_save(chat_id: int) -> None:
    session = SESSIONS.get(chat_id) or {}
    parsed = session.get("parsed")
    actor_name = session.get("actor_name") or "Telegram user"
    if not parsed:
        send_message(chat_id, "Nothing to save — start with Smart Capture.", main_menu_markup())
        return
    cat, payload = smart_payload_to_record(parsed, actor_name)
    record_id, error = create_record(cat, payload, actor_name, chat_id)
    if error:
        send_message(chat_id, f"Save failed: {error}", main_menu_markup())
    else:
        label = GUIDED_FLOWS[cat]["label"]
        send_message(chat_id, f"✅ Saved to *{label}* as record #{record_id}.", main_menu_markup())
    SESSIONS.pop(chat_id, None)


def smart_capture_manual(chat_id: int) -> None:
    """User wants to override: drop into the guided flow for the parsed category."""
    session = SESSIONS.get(chat_id) or {}
    parsed = session.get("parsed") or {}
    cat = parsed.get("category")
    SESSIONS.pop(chat_id, None)
    if cat in GUIDED_FLOWS:
        start_guided_flow(chat_id, cat)
    else:
        send_message(chat_id, "Pick a category.", category_markup())


def handle_callback(callback: dict):
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user = callback.get("from", {})
    answer_callback(callback["id"])
    if not chat_id:
        return

    if data == "smart:save":
        smart_capture_save(chat_id)
        return
    if data == "smart:cancel":
        SESSIONS.pop(chat_id, None)
        send_message(chat_id, "Smart Capture canceled.", main_menu_markup())
        return
    if data == "smart:manual":
        smart_capture_manual(chat_id)
        return
    if not is_authorized(chat_id):
        send_message(chat_id, "This chat is not paired yet. Use /link CODE first.")
        return
    touch_chat(chat_id, user)
    if data.startswith("cat:"):
        table_name = data.split(":", 1)[1]
        if table_name in GUIDED_FLOWS:
            start_guided_flow(chat_id, table_name)


def fetch_updates(offset: int | None):
    payload = {"timeout": POLL_TIMEOUT}
    if offset is not None:
        payload["offset"] = offset
    response = requests.post(f"{API_BASE}/getUpdates", json=payload, timeout=POLL_TIMEOUT + 10)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error during getUpdates: {data}")
    return data["result"]


def main():
    me = telegram_api("getMe")
    LOG.info("Connected to Telegram bot @%s", me.get("username", "unknown"))
    offset = None
    while True:
        try:
            updates = fetch_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_text_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])
        except requests.RequestException as exc:
            LOG.warning("Telegram network error: %s", exc)
            time.sleep(4)
        except Exception as exc:  # pragma: no cover - defensive worker loop
            LOG.exception("Telegram bot loop error: %s", exc)
            time.sleep(4)


if __name__ == "__main__":
    main()
