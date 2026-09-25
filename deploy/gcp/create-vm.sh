#!/bin/bash
# Create the Always Free e2-micro that runs Between.
# An in-use public IPv4 is about $0.005/hour. The VM and 30 GB standard disk are not.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${NAME:-between}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
ADDRESS_NAME="${ADDRESS_NAME:-between-ip}"

if ! gcloud compute addresses describe "$ADDRESS_NAME" --region="$REGION" >/dev/null 2>&1; then
  gcloud compute addresses create "$ADDRESS_NAME" --region="$REGION"
fi

if ! gcloud compute firewall-rules describe between-http >/dev/null 2>&1; then
  gcloud compute firewall-rules create between-http \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:80,tcp:443 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=between-web
fi

if gcloud compute instances describe "$NAME" --zone="$ZONE" >/dev/null 2>&1; then
  echo "VM $NAME already exists in $ZONE."
else
  gcloud compute instances create "$NAME" \
    --zone="$ZONE" \
    --machine-type=e2-micro \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=30GB \
    --boot-disk-type=pd-standard \
    --tags=between-web \
    --address="$ADDRESS_NAME" \
    --metadata-from-file=startup-script="$ROOT/deploy/gcp/startup.sh"
fi

IP="$(gcloud compute addresses describe "$ADDRESS_NAME" --region="$REGION" --format='value(address)')"
echo
echo "Public IP: $IP"
echo "Point an A record at this address, then follow deploy/gcp/RUNBOOK.md from step 2."
