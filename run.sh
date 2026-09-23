#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — edit the two account names and passwords before sharing."
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

echo "Between is at http://127.0.0.1:8000"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
