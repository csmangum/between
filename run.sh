#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  # Portable in-place edit (GNU and BSD sed differ on -i).
  tmp="$(mktemp)"
  sed "s|^SECRET_KEY=.*|SECRET_KEY=${secret}|" .env > "$tmp" && mv "$tmp" .env
  chmod 600 .env
  echo "Created .env with a fresh SECRET_KEY."
  echo "Set USER1_PASSWORD and USER2_PASSWORD (or *_PASSWORD_HASH from 'python -m app.passwords'),"
  echo "then run ./run.sh again."
  exit 1
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
extra=()
if [ "${DEV:-}" = "1" ]; then
  extra+=(--reload)
fi

echo "Between is at http://${HOST}:${PORT}"
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo "Listening beyond this machine without TLS. Put an HTTPS proxy in front (see README)."
fi
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --no-server-header ${extra[@]+"${extra[@]}"}
