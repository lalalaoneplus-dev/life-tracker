#!/usr/bin/env bash
# Live smoke test: boots uvicorn against a THROWAWAY temp DB (real data untouched),
# then exercises read / toggle / bearer-write+readback / 401. The bearer is read from
# the env into a variable and never printed.
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${HOME}/.config/life-tracker/env"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }
# Parse line-by-line (not `source`): the DB path value contains a space.
while IFS='=' read -r k v; do
  case "$k" in LIFE_TRACKER_*) export "$k=$v" ;; esac
done < "$ENV_FILE"

# Isolate the smoke run to a throwaway DB dir so real data is never touched.
TMPDIR2="$(mktemp -d -t lt_smoke)"
export LIFE_TRACKER_DB="$TMPDIR2/smoke.db"
BASE="http://127.0.0.1:8787"
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"

"$PY" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8787 --log-level warning &
SRV=$!
cleanup() { kill "$SRV" 2>/dev/null || true; wait "$SRV" 2>/dev/null || true; rm -rf "$TMPDIR2"; }
trap cleanup EXIT

# wait for the port
for _ in $(seq 1 40); do
  curl -sf -o /dev/null "$BASE/api/session" && break || sleep 0.25
done

AUTH=(-H "Authorization: Bearer ${LIFE_TRACKER_AGENT_TOKEN}")
CT=(-H "Content-Type: application/json")
TODAY="$(date -u +%F)"
fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }

echo "1) 401 without auth"
code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/today")"
[ "$code" = "401" ] || fail "expected 401, got $code"
echo "   ok (401)"

echo "2) GET /api/today (bearer read)"
code="$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$BASE/api/today")"
[ "$code" = "200" ] || fail "today read got $code"
echo "   ok (200)"

echo "3) bearer write: create habit"
HID="$(curl -s "${AUTH[@]}" "${CT[@]}" -X POST "$BASE/api/habits" \
  -d '{"slug":"smoke-habit","title":"Smoke habit","schedule":"daily"}' \
  | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
[ -n "$HID" ] || fail "no habit id returned"
echo "   ok (habit id=$HID)"

echo "4) toggle completion (done=true)"
DONE="$(curl -s "${AUTH[@]}" "${CT[@]}" -X POST "$BASE/api/complete" \
  -d "{\"kind\":\"habit\",\"id\":$HID,\"date\":\"$TODAY\",\"done\":true}" \
  | "$PY" -c 'import sys,json;print(json.load(sys.stdin)["done_today"])')"
[ "$DONE" = "True" ] || fail "toggle did not set done_today (got $DONE)"
echo "   ok (done_today=True)"

echo "5) read back /api/today shows completion"
BACK="$(curl -s "${AUTH[@]}" "$BASE/api/today?date=$TODAY" \
  | "$PY" -c "import sys,json;d=json.load(sys.stdin);print(any(h['id']==$HID and h['done_today'] for h in d['habits']))")"
[ "$BACK" = "True" ] || fail "completion not reflected in today"
echo "   ok (read-back confirms)"

echo "SMOKE PASS"
