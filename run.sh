#!/usr/bin/env bash
# Load local config and start the app on 127.0.0.1:8787 (localhost only, never exposed).
# Parsed line-by-line (not `source`) so values containing spaces are handled correctly.
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${HOME}/.config/life-tracker/env"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
while IFS='=' read -r k v; do
  case "$k" in LIFE_TRACKER_*) export "$k=$v" ;; esac
done < "$ENV_FILE"

PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8787 "$@"
