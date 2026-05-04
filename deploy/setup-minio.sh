#!/usr/bin/env bash
# Bring up the MinIO container and ensure the tasktrack-attachments bucket
# exists. Idempotent — safe to re-run.
#
# Usage:
#   cd ~/projects/collab-tracker
#   ./deploy/setup-minio.sh
#
# Requires: docker, docker compose plugin. deploy/minio.env must exist.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="deploy/minio.env"
COMPOSE_FILE="deploy/docker-compose.minio.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE — copy deploy/minio.env.example and fill in real credentials" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${MINIO_ROOT_USER:?MINIO_ROOT_USER not set in $ENV_FILE}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD not set in $ENV_FILE}"
: "${MINIO_BUCKET:=tasktrack-attachments}"

echo "==> bringing up MinIO container"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo "==> waiting for MinIO to report healthy"
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' tasktrack-minio 2>/dev/null || echo starting)
  if [[ "$status" == "healthy" ]]; then break; fi
  sleep 2
done
if [[ "$status" != "healthy" ]]; then
  echo "MinIO did not become healthy (last status: $status)" >&2
  docker logs --tail 40 tasktrack-minio >&2 || true
  exit 1
fi

echo "==> ensuring bucket '$MINIO_BUCKET' exists"
docker exec tasktrack-minio sh -c "
  mc alias set local http://127.0.0.1:9000 \"$MINIO_ROOT_USER\" \"$MINIO_ROOT_PASSWORD\" >/dev/null 2>&1
  if mc ls local/\"$MINIO_BUCKET\" >/dev/null 2>&1; then
    echo 'bucket already exists'
  else
    mc mb local/\"$MINIO_BUCKET\"
  fi
  mc anonymous set none local/\"$MINIO_BUCKET\" >/dev/null
"

echo "==> done. MinIO API on http://127.0.0.1:9000  console on http://127.0.0.1:9001"
