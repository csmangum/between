#!/bin/bash
# Copy the committed tree to the VM. No .env, no data, no caches, no tests.
# deploy/gcp/.env and the archive stay where they already are on the VM.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${NAME:-between}"
ZONE="${ZONE:-us-central1-a}"

if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]]; then
  echo "Uncommitted changes in $ROOT. Commit (or stash) first; only committed files are pushed." >&2
  exit 1
fi

git -C "$ROOT" archive --format=tar HEAD \
  ':!tests' ':!.env.example' ':!deploy/gcp/.env.example' \
| gzip \
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
  chmod 600 "$tmp/deploy/gcp/.env"
fi
if [ -d "$HOME/between/backups" ]; then
  cp -a "$HOME/between/backups" "$tmp/backups"
fi
if [ -d "$HOME/between" ]; then
  mv "$HOME/between" "$old"
fi
mv "$tmp" "$HOME/between"
rm -rf "$old"
'

echo "Copied to ${NAME}:~/between"
