#!/usr/bin/env bash
# One-line macOS installer. Safe when piped (curl | bash) and when run from a checkout.
set -euo pipefail

DEFAULT_INSTALL_DIR="${HOME}/life-tracker"
DEFAULT_REPO_URL="https://github.com/lalalaoneplus-dev/life-tracker.git"
DEFAULT_TARBALL_URL="https://github.com/lalalaoneplus-dev/life-tracker/archive/refs/heads/main.tar.gz"
APP_PORT="${PORT:-8787}"
APP_URL="http://127.0.0.1:${APP_PORT}"

die() {
  echo "$*" >&2
  exit 1
}

require_macos() {
  local sys
  sys="$(uname -s)"
  if [[ "$sys" != "Darwin" ]]; then
    die "This installer runs on macOS. Detected ${sys}."
  fi
}

looks_like_repo() {
  local d=$1
  [[ -f "${d}/run.sh" && -f "${d}/requirements.txt" && -d "${d}/app" ]]
}

script_checkout() {
  local src="${BASH_SOURCE[0]:-}"
  case "$src" in
    ""|"-"|"bash"|/dev/fd/*|/proc/self/fd/*) return 1 ;;
  esac
  [[ -f "$src" ]] || return 1
  local dir
  dir="$(cd "$(dirname "$src")" && pwd)"
  looks_like_repo "$dir" || return 1
  printf '%s\n' "$dir"
}

ensure_uv() {
  export PATH="${HOME}/.local/bin:${PATH}"
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1 || die "uv is required. It installs into ${HOME}/.local/bin."
}

sync_source() {
  local dest=$1
  local url="${REPO_URL:-$DEFAULT_REPO_URL}"
  local tarball="${TARBALL_URL:-$DEFAULT_TARBALL_URL}"

  mkdir -p "$dest"

  if [[ -d "$url" ]]; then
    url="$(cd "$url" && pwd)"
    dest="$(cd "$dest" && pwd)"
    if [[ "$url" == "$dest" ]]; then
      return 0
    fi
    /usr/bin/rsync -a \
      --exclude '.git/' \
      --exclude '.venv/' \
      --exclude '.run/' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '*.log' \
      --exclude '.env' \
      "${url}/" "${dest}/"
    return 0
  fi

  if command -v git >/dev/null 2>&1; then
    if [[ -d "${dest}/.git" ]]; then
      git -C "$dest" pull --ff-only
      return 0
    fi
    if [[ -z "$(ls -A "$dest" 2>/dev/null || true)" ]]; then
      git clone "$url" "$dest"
      return 0
    fi
    return 0
  fi

  local tmp inner
  tmp="$(mktemp -d)"
  curl -fsSL "$tarball" -o "${tmp}/src.tar.gz"
  tar -xzf "${tmp}/src.tar.gz" -C "$tmp"
  for inner in "$tmp"/*; do
    if [[ -d "$inner" ]]; then
      /usr/bin/rsync -a --exclude '.env' "${inner}/" "${dest}/"
      break
    fi
  done
  rm -rf "$tmp"
}

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

port_busy() {
  local port=$1
  local bin
  bin="$(lsof_cmd)" || return 1
  if "$bin" -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

wait_http() {
  local url=$1
  local i=0
  while [[ "$i" -lt 90 ]]; do
    if curl -fsS -o /dev/null "$url"; then
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done
  die "Timed out waiting for ${url}"
}

write_env_if_missing() {
  local env_dir="${HOME}/.config/life-tracker"
  local env_file="${env_dir}/env"
  local db_dir="${HOME}/Library/Application Support/life-tracker"
  local db_path="${db_dir}/tracker.db"
  local py token secret

  mkdir -p "$env_dir" "$db_dir"
  if [[ -f "$env_file" ]]; then
    return 0
  fi

  py="${REPO_DIR}/.venv/bin/python"
  [[ -x "$py" ]] || die "Python 3.12 virtualenv is missing in ${REPO_DIR}/.venv."
  token="$("$py" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  secret="$("$py" -c 'import secrets; print(secrets.token_urlsafe(32))')"

  umask 077
  cat > "$env_file" <<EOF
LIFE_TRACKER_AGENT_TOKEN=${token}
LIFE_TRACKER_SESSION_SECRET=${secret}
LIFE_TRACKER_DB=${db_path}
EOF
  chmod 600 "$env_file"
}

require_macos

if [[ ! "$APP_PORT" =~ ^[0-9]+$ ]]; then
  die "PORT must be a number."
fi

REPO_DIR=""
if checkout="$(script_checkout 2>/dev/null || true)" && [[ -n "${checkout}" && -z "${INSTALL_DIR:-}" ]]; then
  REPO_DIR="$checkout"
elif [[ -z "${INSTALL_DIR:-}" && -z "${REPO_URL:-}" ]] && looks_like_repo "$PWD"; then
  REPO_DIR="$PWD"
else
  REPO_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
  sync_source "$REPO_DIR"
fi

looks_like_repo "$REPO_DIR" || die "life-tracker files were not found in ${REPO_DIR}"

ensure_uv

if [[ ! -x "${REPO_DIR}/.venv/bin/python" ]]; then
  uv venv --python 3.12 "${REPO_DIR}/.venv"
fi
uv pip install -r "${REPO_DIR}/requirements.txt" --python "${REPO_DIR}/.venv/bin/python"

write_env_if_missing

chmod +x "${REPO_DIR}/run.sh" "${REPO_DIR}/stop.sh" 2>/dev/null || true

if [[ "${NO_START:-}" != "1" ]]; then
  bash "${REPO_DIR}/stop.sh"
  sleep 0.3
  if port_busy "$APP_PORT"; then
    sleep 0.5
  fi
  if port_busy "$APP_PORT"; then
    die "Port ${APP_PORT} is in use. Choose another with PORT=<port>."
  fi
  mkdir -p "${REPO_DIR}/.run"
  LIFE_TRACKER_PIDFILE="${REPO_DIR}/.run/life-tracker.pid" \
    PORT="${APP_PORT}" \
    nohup "${REPO_DIR}/run.sh" >"${REPO_DIR}/life-tracker.log" 2>&1 &
  wait_http "${APP_URL}/"
  if [[ "${NO_OPEN:-}" != "1" ]]; then
    open "$APP_URL"
  fi
fi

echo "life-tracker is at ${APP_URL}"
echo "Start again with: ${REPO_DIR}/run.sh"
echo "Stop with: ${REPO_DIR}/stop.sh"
echo "Config: ${HOME}/.config/life-tracker/env"
