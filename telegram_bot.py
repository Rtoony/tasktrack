#!/usr/bin/env python3
"""TaskTrack Telegram bot worker."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import date, datetime, timedelta

import requests

from app import ALLOWED_TABLES, DB_PATH, validate_record_data


LOG = logging.getLogger("tasktrack.telegram_bot")
logging.basicConfig(
    level=os.environ.get("TASKTRACK_TELEGRAM_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)


BOT_TOKEN = (
    os.environ.get("TASKTRACK_TELEGRAM_TOKEN")
    or os.environ.get("MYTRACK_TELEGRAM_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
)
if not BOT_TOKEN:
    raise SystemExit("Telegram bot token not found in environment")

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_TIMEOUT = 45
REQUEST_TIMEOUT = 60
SESSIONS: dict[int, dict] = {}


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


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def load_link_code():
    conn = db_connect()
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'telegram_link_code'").fetchone()
    conn.close()
    return row["value"] if row else ""


def is_authorized(chat_id: int):
    if chat_id in ENV_ALLOWED_CHAT_IDS:
        return True
    conn = db_connect()
    row = conn.execute(
        "SELECT 1 FROM telegram_chat_access WHERE chat_id = ? AND is_active = 1",
        (chat_id,),
    ).fetchone()
    conn.close()
    return bool(row)


def upsert_linked_chat(user, chat):
    username = user.get("username", "") or ""
    display_name = display_name_for_user(user)
    conn = db_connect()
    conn.execute(
        "INSERT INTO telegram_chat_access (chat_id, username, display_name, linked_at, last_seen_at, is_active) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1) "
        "ON CONFLICT(chat_id) DO UPDATE SET "
        "username = excluded.username, display_name = excluded.display_name, last_seen_at = CURRENT_TIMESTAMP, is_active = 1",
        (chat["id"], username, display_name),
    )
    conn.commit()
    conn.close()


def touch_chat(chat_id: int, user):
    conn = db_connect()
    conn.execute(
        "UPDATE telegram_chat_access SET username = ?, display_name = ?, last_seen_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
        (user.get("username", "") or "", display_name_for_user(user), chat_id),
    )
    conn.commit()
    conn.close()


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
            [{"text": "New Task"}, {"text": "Quick CAD"}],
            [{"text": "Quick Suggestion"}, {"text": "Help"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


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


def create_record(table_name: str, payload: dict, actor_name: str):
    validation_payload = dict(payload)
    error = validate_record_data(table_name, validation_payload, creating=True)
    if error:
        return None, error

    cfg = ALLOWED_TABLES[table_name]
    for req in cfg["required"]:
        if not str(validation_payload.get(req, "")).strip():
            return None, f"{req} is required"

    fields = [f for f in (cfg["fields"] + ["created_by_user_id", "created_by_name"]) if f in validation_payload]
    values = [validation_payload[f] for f in fields]
    placeholders = ", ".join(["?"] * len(fields))
    conn = db_connect()
    cur = conn.execute(
        f"INSERT INTO {table_name} ({', '.join(fields)}) VALUES ({placeholders})",
        values,
    )
    record_id = cur.lastrowid
    title = validation_payload.get("title") or validation_payload.get("person_name") or ""
    conn.execute(
        "INSERT INTO activity_log (table_name, record_id, action, field_name, old_value, new_value, user_name) VALUES (?, ?, ?, '', '', ?, ?)",
        (table_name, record_id, "telegram_created", title, f"Telegram: {actor_name}"),
    )
    conn.commit()
    conn.close()
    return record_id, None


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

    record_id, error = create_record(table_name, data, actor_name)
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
    record_id, error = create_record(session["table"], payload, actor_name)
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
        code = text.split(" ", 1)[1].strip().upper()
        expected = load_link_code().strip().upper()
        if code and expected and code == expected:
            upsert_linked_chat(user, chat)
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

    if handle_guided_input(chat_id, user, text):
        return
    if handle_quick_input(chat_id, user, text):
        return

    send_message(chat_id, "Use `New Task`, `Quick CAD`, or `Quick Suggestion` to start.", main_menu_markup())


def handle_callback(callback: dict):
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user = callback.get("from", {})
    answer_callback(callback["id"])
    if not chat_id:
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
