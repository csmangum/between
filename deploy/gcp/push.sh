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
| gcloud compute ssh "$NAME" --zone="$ZONE" --command='
set -euo pipefail
tmp="$HOME/between.next"
old="$HOME/between.old"
rm -rf "$tmp" "$old"
mkdir -p "$tmp"
tar -xzf - -C "$tmp"
if [ -f "$HOME/between/deploy/gcp/.env" ]; then
  mkdir -p "$tmp/deploy/gcp"
  cp "$HOME/between/deploy/gcp/.env" "$tmp/deploy/gcp/.env"
fi
if [ -d "$HOME/between/backups" ]; then
  cp -a "$HOME/between/backups" "$tmp/backups"
fi
if [ -d "$HOME/between" ]; then
  find "$HOME/between" -maxdepth 1 -type f -name "*.tgz" -exec cp {} "$tmp/" \;
  mv "$HOME/between" "$old"
fi
mv "$tmp" "$HOME/between"
rm -rf "$old"
'

echo "Copied to ${NAME}:~/between"
