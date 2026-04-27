#!/usr/bin/env bash
set -euo pipefail

cd /home/rtoony/projects/collab-tracker

if [ -z "${BW_SESSION:-}" ] && [ -f /dev/shm/nexus_session ]; then
  export BW_SESSION
  BW_SESSION=$(cat /dev/shm/nexus_session)
fi

TASKTRACK_TELEGRAM_TOKEN=$(
  bw get item BR_TRACK_BOT 2>/dev/null \
    | jq -r '.fields[]? | select(.name == "TELEGRAM_BOT_TOKEN") | .value' \
    | head -n 1
)

if [ -z "${TASKTRACK_TELEGRAM_TOKEN:-}" ] || [ "${TASKTRACK_TELEGRAM_TOKEN}" = "null" ]; then
  echo "BR_TRACK_BOT TELEGRAM_BOT_TOKEN field not found in Vaultwarden" >&2
  exit 1
fi

export TASKTRACK_TELEGRAM_TOKEN

# Phase 1C-b: bot now talks to TaskTrack via REST instead of touching
# SQLite directly. Pull a bot-scoped token (or legacy single token) from
# the collab-tracker env file (vault-injected by its systemd unit).
# Long-term: add a TASKTRACK_TOKEN_BOT field to the BR_TRACK_BOT vault
# item and pull it here directly via `bw get`.
COLLAB_ENV=/dev/shm/nexus-env-collab-tracker
if [ -f "$COLLAB_ENV" ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      TASKTRACK_TOKEN_BOT|TASKTRACK_TOKEN)
        # Strip optional surrounding quotes.
        value="${value%\"}"; value="${value#\"}"
        export "$key=$value"
        ;;
    esac
  done < "$COLLAB_ENV"
fi

if [ -z "${TASKTRACK_TOKEN_BOT:-}" ] && [ -z "${TASKTRACK_TOKEN:-}" ]; then
  echo "TASKTRACK_TOKEN_BOT (or legacy TASKTRACK_TOKEN) not found in env or $COLLAB_ENV" >&2
  exit 1
fi

exec /home/rtoony/miniconda3/bin/python3 telegram_bot.py
