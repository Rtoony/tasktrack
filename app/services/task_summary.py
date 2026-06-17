"""Local-AI task summaries (feedback #34).

A homelab model (via the Nexus LiteLLM gateway) drafts a short, plain-language
summary of a task from its current fields. Suggest-and-confirm: this only
RETURNS a draft — the operator accepts/edits it, and the normal record PUT
persists it into `ai_summary`. Nothing here writes the DB.

Local-first by default (TASKTRACK_SUMMARY_MODEL=gemma4-26b — a valid local
alias on the gateway, verified to produce clean prose ~5s); generous max_tokens
so a reasoning model doesn't truncate to empty (the documented local-model
reasoning-burn failure mode).
"""
import os
import re
import time

import requests

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = (
    os.environ.get("LITELLM_API_KEY")
    or os.environ.get("LITELLM_MASTER_KEY")
    or ""
)
SUMMARY_MODEL = os.environ.get("TASKTRACK_SUMMARY_MODEL", "gemma4-26b")
SUMMARY_TIMEOUT_S = int(os.environ.get("TASKTRACK_SUMMARY_TIMEOUT_S", "90"))
SUMMARY_MAX_TOKENS = int(os.environ.get("TASKTRACK_SUMMARY_MAX_TOKENS", "1200"))

# Which fields feed the summary, per table (richest signal first; blanks skipped).
_SUMMARY_FIELDS = {
    "project_work_tasks": [
        ("project_number", "Project #"), ("project_name", "Project"),
        ("title", "Title"), ("billing_phase", "Phase"), ("task_description", "Description"),
        ("status", "Status"), ("priority", "Priority"),
        ("scope_notes", "Scope notes"), ("progress_notes", "Progress notes"), ("notes", "Notes"),
    ],
    "work_tasks": [
        ("title", "Title"), ("category", "Work stream"), ("cad_skill_area", "Skill area"),
        ("description", "Description"), ("software", "Software"),
        ("status", "Status"), ("priority", "Priority"),
        ("starter_note", "Starter note"), ("clarifications_needed", "Open questions"), ("notes", "Notes"),
    ],
}

SUMMARY_TABLES = tuple(_SUMMARY_FIELDS.keys())

_SYSTEM_PROMPT = (
    "You are a civil-engineering project assistant. Write a SHORT, plain-language "
    "summary of what a task is and what doing it would entail — the kind of one-glance "
    "context a busy engineer wants. 2-3 sentences, no preamble, no bullet points, no "
    "markdown, no restating the due date or priority. Be concrete and specific to the "
    "task; if the inputs are thin, summarize what little is there without inventing scope."
)


def _digest(table, record):
    rows = []
    for key, label in _SUMMARY_FIELDS.get(table, []):
        val = str(record.get(key) or "").strip()
        if val:
            rows.append(f"{label}: {val}")
    return "\n".join(rows)


def _strip_reasoning(text):
    """Local reasoning models may emit <think>…</think> before the answer — drop it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    text = re.sub(r"```.*?```", "", text, flags=re.S).strip()
    return text


def draft_summary(table, record):
    """Return a plain-text summary draft for the task, or raise RuntimeError.

    `record` is a dict of the task's fields (e.g. from to_dict). Does NOT persist.
    """
    if table not in _SUMMARY_FIELDS:
        raise ValueError(f"summaries not supported for table {table!r}")
    digest = _digest(table, record)
    if not digest:
        raise RuntimeError("task has no content to summarize yet")

    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
    payload = {
        "model": SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"TASK FIELDS:\n{digest}\n\nWrite the summary."},
        ],
        "temperature": 0.2,
        "max_tokens": SUMMARY_MAX_TOKENS,
    }
    last_exc = None
    resp = None
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f"{LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions",
                headers=headers, json=payload, timeout=SUMMARY_TIMEOUT_S,
            )
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt == 1:
                time.sleep(2)
    if resp is None:
        raise RuntimeError(f"summary model unreachable: {last_exc}")
    resp.raise_for_status()
    content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    summary = _strip_reasoning(content)
    if not summary:
        raise RuntimeError("summary model returned empty output (try again)")
    return summary[:1500]
