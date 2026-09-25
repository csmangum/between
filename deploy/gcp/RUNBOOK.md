# Deploy Between on Google Cloud

One `e2-micro` VM in `us-central1`, Caddy for HTTPS, and the two accounts in `deploy/gcp/.env`. Chris and Karin open the site in a browser. Cloudflare is not part of this path.

The VM and its 30 GB standard disk stay inside Always Free. The public IPv4 address is about **$0.005 per hour** (about $3.65 per month). Stopping the VM pauses compute usage, but does not remove external IP charges. To avoid the IP charge, release the reservation (and lose that address), then reserve a new one when you start the VM again.

You need:

- `gcloud` logged in, with a billing account and a budget alert at a few dollars
- a domain you control, and access to its DNS
- this repo on the machine where you run the commands below

## 1. Create the VM

```bash
chmod +x deploy/gcp/*.sh
./deploy/gcp/create-vm.sh
```

The script prints a public IP. Create an **A record** at your DNS host for the name you will use, for example `between.example.com`, pointing at that IP. This can be any registrar. Wait until the name resolves:

```bash
dig +short between.example.com
```

The startup script installs Docker and a 1 GB swap file. Give it a couple of minutes, then:

```bash
gcloud compute ssh between --zone=us-central1-a
docker --version
exit
```

If `docker` is not there yet, wait and try the SSH command again.

## 2. Put the app on the VM

From your checkout:

```bash
./deploy/gcp/push.sh
```

SSH in and write the account file. Passwords and `SECRET_KEY` must be long random strings, not the samples.

```bash
gcloud compute ssh between --zone=us-central1-a
cd ~/between
cp deploy/gcp/.env.example deploy/gcp/.env
nano deploy/gcp/.env
```

Set:

| Variable | Value |
|---|---|
| `SITE_ADDRESS` | the name in the A record, such as `between.example.com` |
| `ACME_EMAIL` | an email Let's Encrypt can use for expiry notices |
| `HTTPS_ONLY` | `true` |
| `USER1_NAME` / `USER1_DISPLAY` | `chris` / `Chris` |
| `USER1_PASSWORD` | Chris's password |
| `USER2_NAME` / `USER2_DISPLAY` | `karin` / `Karin` |
| `USER2_PASSWORD` | Karin's password |
| `SECRET_KEY` | a different long random string |

## 3. Start it

Still on the VM, from `~/between`:

```bash
sudo docker compose --env-file deploy/gcp/.env -f deploy/gcp/docker-compose.yml up -d --build
sudo docker compose --env-file deploy/gcp/.env -f deploy/gcp/docker-compose.yml ps
```

Caddy asks Let's Encrypt for a certificate over port 80. The first start can take a minute. If it keeps restarting, read the log:

```bash
sudo docker compose --env-file deploy/gcp/.env -f deploy/gcp/docker-compose.yml logs --tail=80 caddy
```

The usual cause is that `SITE_ADDRESS` does not point at this VM yet.

On the VM, confirm the app itself:

```bash
curl -fsS http://127.0.0.1:8000/health
```

You want `"ok": true`. Port 8000 is bound to the VM's loopback only. Browsers use 443.

## 4. Test the deployment

On your own machine, with the same `deploy/gcp/.env` you wrote (copy it back, or recreate it locally and do not commit it):

```bash
python3 deploy/gcp/smoke_test.py --base-url https://between.example.com --env deploy/gcp/.env
```

The script logs in as both people, checks that a private page stays hidden, opens an offer, sends a chat line, and pulls the topic back. A passing run ends with `Between is up.`

Then in a browser:

1. Open `https://between.example.com`. The certificate should be for your domain.
2. Sign in as Chris. Write a topic and leave it on the desk.
3. Sign in as Karin in a private window. The topic is absent.
4. As Chris, offer it. As Karin, the title is visible and the body is not. Open it.
5. Send a chat line from each window. Both see it.
6. As Chris, pull it back. Karin loses the body, and the margin closes.

## 5. After a change

```bash
./deploy/gcp/push.sh
gcloud compute ssh between --zone=us-central1-a --command \
  'cd ~/between && sudo docker compose --env-file deploy/gcp/.env -f deploy/gcp/docker-compose.yml up -d --build'
python3 deploy/gcp/smoke_test.py --base-url https://between.example.com --env deploy/gcp/.env
```

`deploy/gcp/.env` on the VM is not in the copy. The archive lives in the `between_between-data` Docker volume, not in the git checkout.

## Backup

From the VM, stop writes before copying the `between_between-data` volume:

```bash
cd ~/between
sudo mkdir -p backups
sudo bash -lc 'set -euo pipefail; trap "docker compose --env-file deploy/gcp/.env -f deploy/gcp/docker-compose.yml up -d" EXIT; \
  docker compose --env-file deploy/gcp/.env -f deploy/gcp/docker-compose.yml stop between caddy; \
  docker run --rm -v between_between-data:/data -v "$PWD":/backup alpine tar czf /backup/backups/between-data.tgz -C /data .'
```

Copy `backups/between-data.tgz` off the VM and confirm restore in a throwaway directory before relying on it.

## Stop and start

```bash
gcloud compute instances stop between --zone=us-central1-a
gcloud compute instances start between --zone=us-central1-a
```

Docker is set to come back on boot. The IP stays the same because it is reserved.
