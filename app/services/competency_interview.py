"""Prelim "describe → rate" batch interview (feedback #51 Wave 3).

Josh describes an employee's CAD/GIS abilities in plain language; a LOCAL homelab
model maps that to a preliminary 0-3 rating per active skill category (the
growth ladder: 0 Learning, 1 Developing, 2 Capable, 3 Mentor — null = no signal /
N/A), plus a strengths-first snapshot and a trajectory flag. SUGGEST-AND-CONFIRM:
this only drafts; the route saves what Josh confirms via the existing
preliminary_rating path. Nothing here writes the DB.
"""
import json
import os
import re
import time

import requests

from ..config import COMPETENCY_LEVELS

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = (
    # master first: a stale/scoped LITELLM_API_KEY in some envs shadows the working
    # master key and 401s the gateway (bit the lab). Master is always valid.
    os.environ.get("LITELLM_MASTER_KEY")
    or os.environ.get("LITELLM_API_KEY")
    or ""
)
INTERVIEW_MODEL = os.environ.get("TASKTRACK_INTERVIEW_MODEL", "gemma4-26b")
INTERVIEW_TIMEOUT_S = int(os.environ.get("TASKTRACK_INTERVIEW_TIMEOUT_S", "120"))
INTERVIEW_MAX_TOKENS = int(os.environ.get("TASKTRACK_INTERVIEW_MAX_TOKENS", "1600"))


def _levels_text():
    return "; ".join(f"{lv['score']}={lv['label']} ({lv['decision']})" for lv in COMPETENCY_LEVELS)


def _extract_json(text):
    """Pull the first JSON object out of a model reply (handles fences/prose/<think>)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    cands = ([m.group(1)] if m else []) + [text]
    dec = json.JSONDecoder()
    for cand in cands:
        for i, ch in enumerate(cand):
            if ch == "{":
                try:
                    return dec.raw_decode(cand, i)[0]
                except Exception:
                    continue
    return None


def draft_interview_ratings(employee_name, employee_role, categories, description):
    """Map a plain-language description to a preliminary per-category rating draft.

    `categories` = [{"id", "name", "description"}, ...] (active rubric).
    Returns {ratings:[{category_id, score|None, note}], strengths, growth, trajectory}
    or raises RuntimeError. Does NOT persist.
    """
    description = (description or "").strip()
    if not description:
        raise RuntimeError("describe the employee first")
    cat_lines = "\n".join(f'- id {c["id"]}: {c["name"]} — {c.get("description","")}' for c in categories)
    valid_ids = [c["id"] for c in categories]

    system = (
        "You are a civil-engineering CAD manager helping set a PRELIMINARY, non-punitive "
        "capability baseline. Map a manager's plain-language read of one employee to a 0-3 "
        "score per skill category on this GROWTH ladder: " + _levels_text() + ". A score is a "
        "capability/decision level, never a judgment of the person. Use null when the note gives "
        "no signal for a category (don't guess wildly). Lead with strengths; frame gaps as "
        "'next to learn', never 'deficient'. Respond with JSON ONLY, no prose, this exact schema:\n"
        '{"ratings":[{"category_id":int,"score":int|null,"note":str}],'
        '"strengths":str,"growth":str,"trajectory":"rising"|"steady"}\n'
        "One ratings entry per category id given. note = <=12 words, positive. strengths/growth = "
        "one short sentence each. trajectory = your read of their direction."
    )
    user = (
        f"EMPLOYEE: {employee_name} (role: {employee_role or 'unknown'})\n"
        f"SKILL CATEGORIES (map a score to each id):\n{cat_lines}\n\n"
        f"MANAGER'S DESCRIPTION:\n{description}\n\nReturn the JSON."
    )

    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
    payload = {
        "model": INTERVIEW_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": INTERVIEW_MAX_TOKENS,
        # NB: no response_format — not all local gateway models support it (500s on
        # gemma); the prompt demands JSON and _extract_json handles fences/prose.
    }
    last_exc = None
    resp = None
    for attempt in (1, 2):
        try:
            resp = requests.post(
                f"{LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions",
                headers=headers, json=payload, timeout=INTERVIEW_TIMEOUT_S,
            )
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt == 1:
                time.sleep(2)
    if resp is None:
        raise RuntimeError(f"interview model unreachable: {last_exc}")
    resp.raise_for_status()
    content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
    data = _extract_json(content)
    if not isinstance(data, dict) or "ratings" not in data:
        raise RuntimeError("interview model returned no usable mapping (try again)")

    # Normalize + clamp; keep only known category ids, scores in 0-3 or null.
    clean = []
    seen = set()
    for r in data.get("ratings", []):
        try:
            cid = int(r.get("category_id"))
        except (TypeError, ValueError):
            continue
        if cid not in valid_ids or cid in seen:
            continue
        seen.add(cid)
        score = r.get("score")
        if score is not None:
            try:
                score = int(score)
            except (TypeError, ValueError):
                score = None
            if score is not None and not (0 <= score <= 3):
                score = max(0, min(3, score))
        clean.append({"category_id": cid, "score": score, "note": str(r.get("note") or "")[:120]})
    traj = str(data.get("trajectory") or "steady").strip().lower()
    if traj not in ("rising", "steady"):
        traj = "steady"
    return {
        "ratings": clean,
        "strengths": str(data.get("strengths") or "")[:400],
        "growth": str(data.get("growth") or "")[:400],
        "trajectory": traj,
        "model": INTERVIEW_MODEL,
    }
