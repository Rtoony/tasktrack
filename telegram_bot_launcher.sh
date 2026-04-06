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
exec /home/rtoony/miniconda3/bin/python3 telegram_bot.py
