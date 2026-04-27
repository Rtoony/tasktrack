#!/usr/bin/env bash
# TaskTrack Phase 0 smoke test.
# Verifies the basic public surface is healthy. Exits non-zero on any failure.
# Used to gate Phase 1 work; expand as new endpoints land.

set -euo pipefail

BASE_URL="${TASKTRACK_BASE_URL:-http://127.0.0.1:5050}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
pass() { echo "ok  $*"; }

echo "smoke target: $BASE_URL"

# 1. /healthz returns 200 with body 'ok'
body="$(curl -fsS "$BASE_URL/healthz")" || fail "/healthz did not return 200"
[[ "$body" == "ok" ]] || fail "/healthz body unexpected: '$body'"
pass "/healthz returns 200 ok"

# 2. /login returns 200 and renders the sign-in form
status="$(curl -s -o "$TMP/login.html" -w '%{http_code}' "$BASE_URL/login")"
[[ "$status" == "200" ]] || fail "/login returned $status"
grep -q "Sign in" "$TMP/login.html" || fail "/login did not render login form"
pass "/login returns 200 with sign-in form"

# 3. / redirects to /login when unauthenticated
status="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/")"
[[ "$status" == "302" ]] || fail "/ returned $status, expected 302"
pass "/ redirects to /login when unauthenticated"

# 4. /api/work_tasks rejects unauthenticated session calls
status="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/api/work_tasks")"
[[ "$status" == "401" || "$status" == "302" ]] || fail "/api/work_tasks returned $status, expected 401 or 302"
pass "/api/work_tasks blocks unauthenticated access ($status)"

echo "smoke green"
