#!/usr/bin/env bash
# Load local config and start the app on 127.0.0.1 (localhost only).
# Parsed line-by-line (not `source`) so values containing spaces stay intact.
# Foreground when run directly. The installer sets LIFE_TRACKER_PIDFILE and
# backgrounds this script; the pid file records this process, which remains
# the server after exec.
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${HOME}/.config/life-tracker/env"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
while IFS='=' read -r k v; do
  case "$k" in LIFE_TRACKER_*) export "$k=$v" ;; esac
done < "$ENV_FILE"

ROOT="$(pwd)"
PY="${ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
PORT="${PORT:-8787}"

if [[ -n "${LIFE_TRACKER_PIDFILE:-}" ]]; then
  mkdir -p "$(dirname "$LIFE_TRACKER_PIDFILE")"
  lock="${LIFE_TRACKER_PIDFILE}.lock"
  : >"$lock"
  exec 9>"$lock"
  printf '%s\n' "$$" >"$LIFE_TRACKER_PIDFILE"
fi

exec "$PY" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port "$PORT" "$@"
