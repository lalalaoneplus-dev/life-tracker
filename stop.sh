#!/usr/bin/env bash
# Stop the life-tracker process this install started. Never signals any other pid.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="${ROOT}/.run/life-tracker.pid"

lsof_cmd() {
  if command -v lsof >/dev/null 2>&1; then
    command -v lsof
    return 0
  fi
  if [[ -x /usr/sbin/lsof ]]; then
    printf '%s\n' /usr/sbin/lsof
    return 0
  fi
  return 1
}

holds_lock() {
  local pid=$1
  local lock=$2
  local bin
  bin="$(lsof_cmd)" || return 1
  if "$bin" -nP -p "$pid" 2>/dev/null | grep -F -q -- "$lock"; then
    return 0
  fi
  return 1
}

collect_descendants() {
  local pid=$1
  local child
  if ! command -v pgrep >/dev/null 2>&1; then
    return 0
  fi
  while IFS= read -r child; do
    if [[ -n "$child" ]]; then
      printf '%s\n' "$child"
      collect_descendants "$child"
    fi
  done < <(pgrep -P "$pid" 2>/dev/null || true)
}

stop_recorded() {
  local pidfile=$1
  local pid lock ids child id
  [[ -f "$pidfile" ]] || return 0
  pid="$(tr -d '[:space:]' < "$pidfile")"
  lock="${pidfile}.lock"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    rm -f "$pidfile" "$lock"
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile" "$lock"
    return 0
  fi
  if ! holds_lock "$pid" "$lock"; then
    rm -f "$pidfile" "$lock"
    return 0
  fi
  ids="$pid"
  while IFS= read -r child; do
    if [[ -n "$child" ]]; then
      ids="${ids} ${child}"
    fi
  done < <(collect_descendants "$pid")
  for id in $ids; do
    kill -TERM "$id" 2>/dev/null || true
  done
  sleep 0.4
  for id in $ids; do
    if kill -0 "$id" 2>/dev/null; then
      kill -KILL "$id" 2>/dev/null || true
    fi
  done
  local i=0
  while kill -0 "$pid" 2>/dev/null && [[ "$i" -lt 20 ]]; do
    sleep 0.1
    i=$((i + 1))
  done
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile" "$lock"
  fi
}

stop_recorded "$PIDFILE"
