#!/usr/bin/env bash
set -euo pipefail

# collab-tracker intentionally has no frontend package. Reuse the local
# br-portal node_modules for the React/esbuild toolchain and emit a plain
# static bundle that TaskTrack serves from /static/js/.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_MODULES="${NODE_MODULES:-/home/rtoony/br-portal/node_modules}"
ESBUILD="${ESBUILD:-/home/rtoony/br-portal/node_modules/.bin/esbuild}"

NODE_PATH="$NODE_MODULES" "$ESBUILD" \
  "$ROOT/static/js/br-intake.jsx" \
  --bundle \
  --minify \
  --format=iife \
  --target=es2018 \
  --outfile="$ROOT/static/js/br-intake.bundle.js" \
  --define:process.env.NODE_ENV='"'"'production'"'"'
