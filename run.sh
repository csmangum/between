#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

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

set -a
# shellcheck disable=SC1091
source .env
set +a

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
