#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Password hashes contain `$`. Sourcing the file would expand those as shell variables.
# One pair of matching quotes is removed, and a leading `export ` is ignored.
load_env() {
  local file="${1:-.env}"
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    key="${key#export }"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ ${#value} -ge 2 && ( "$value" == \"*\" || "$value" == \'*\' ) ]]; then
      value="${value:1:${#value}-2}"
    fi
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$file"
}

# Tests call this to print selected keys from a file without starting the app.
if [[ "${1:-}" == "--print-env-file" ]]; then
  load_env "$2"
  shift 2
  for key in "$@"; do
    if [[ -v $key ]]; then
      printf '%s=%s\n' "$key" "${!key}"
    else
      printf '%s=\n' "$key"
    fi
  done
  exit 0
fi

if [ ! -f .env ]; then
  cp .env.example .env
  key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  sed -i.bak "s|^SECRET_KEY=.*|SECRET_KEY=${key}|" .env && rm -f .env.bak
  chmod 600 .env
  echo "Created .env with a fresh SECRET_KEY."
  echo "Edit the two names and passwords (or run 'python -m app.auth' for hashes) before starting."
  exit 1
fi

if [ "$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env)" != "600" ]; then
  echo "warning: .env is readable by other users on this machine; run: chmod 600 .env" >&2
fi

load_env

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
echo "Between is at http://${HOST}:${PORT}"
if [ "$HOST" != "127.0.0.1" ] && [ "${HTTPS_ONLY:-}" = "" ]; then
  echo "warning: listening beyond loopback without HTTPS. Anyone on this network can read the login." >&2
fi
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --no-server-header ${RELOAD:+--reload}
