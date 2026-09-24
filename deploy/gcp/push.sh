#!/bin/bash
# Copy this checkout to the VM. .env and the archive stay where they already are.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${NAME:-between}"
ZONE="${ZONE:-us-central1-a}"

tar -C "$ROOT" \
  --exclude=data \
  --exclude=.venv \
  --exclude=.git \
  --exclude='deploy/gcp/.env' \
  -czf - . \
| gcloud compute ssh "$NAME" --zone="$ZONE" --command='mkdir -p ~/between && tar -xzf - -C ~/between'

echo "Copied to ${NAME}:~/between"
