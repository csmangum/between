# Between

A private room for two. Write until you are ready. Nothing crosses until you send it and they choose to open it.

The first-draft design document is [DESIGN.md](DESIGN.md).

## How access works

Everything starts **private**.

1. You write a topic, a long piece, or a comment. It is stored on this machine under `data/local/<your-name>/`.
2. You **send** it. The other person sees that something was offered — not the body.
3. They **open** it. Only then does the body become readable, and a copy is written to `data/shared/`.
4. You can **pull it back**. Shared access ends.

Chat on a topic stays closed until the *topic itself* is sent and opened. The margin is for after agreement.

## Quick start (local)

```bash
chmod +x run.sh
./run.sh            # first run: writes .env with a random SECRET_KEY, then stops
# set USER1_PASSWORD and USER2_PASSWORD in .env (or *_PASSWORD_HASH, see below)
./run.sh
```

Open `http://127.0.0.1:8000`. The app refuses to start with a placeholder `SECRET_KEY`, a placeholder or short password, or a username that is not a plain lowercase slug. It listens on loopback only; set `HOST=0.0.0.0` deliberately if you want the LAN to reach it, and put TLS in front if you do.

## Host it (Docker)

```bash
cp .env.example .env
# set SECRET_KEY and the two passwords / hashes; keep HTTPS_ONLY=true
docker compose up -d --build
```

Compose binds `127.0.0.1:8000` only and runs the app as an unprivileged user on a read-only filesystem. Put Caddy or nginx with HTTPS in front on the same host, and set `ALLOWED_HOSTS` to your hostname. Data lives in the named volume `between-data`; the compose file explains how to use `./data` instead.

## Accounts

Only two people exist, set in `.env`:

| Variable | Meaning |
|---|---|
| `USER1_NAME` / `USER2_NAME` | login name: lowercase letters, digits, `-`, `_` |
| `USER1_DISPLAY` / `USER2_DISPLAY` | name shown in the UI |
| `USER1_PASSWORD` / `USER2_PASSWORD` | password, at least 12 characters |
| `USER1_PASSWORD_HASH` / `USER2_PASSWORD_HASH` | preferred: PBKDF2 hash from `python -m app.passwords`, so the password itself is never on the host |
| `SECRET_KEY` | signs the session cookie; random, 32+ characters |
| `HTTPS_ONLY` | `true` whenever the app is reached over HTTPS (Secure cookie, HSTS) |
| `ALLOWED_HOSTS` | optional comma-separated hostnames to accept |
| `SESSION_DAYS` | how long a login lasts (default 7) |

Failed logins are throttled per address and per name. Every form post and the chat socket must come from this site's own origin. Pages are served with a strict Content Security Policy and make no third-party requests. The full review is in [SECURITY.md](SECURITY.md).

## Where the files live

```
data/
  between.db              # local SQLite
  local/<user>/...        # each person's private markdown
  shared/...              # copies written only after agreement
```

Archive and export only include what *you* are allowed to read: your private pages plus anything both of you accepted.

Everything under `data/` is created owner-only (`0700` directories, `0600` files). Pulling something back removes it from `data/shared/`.

## Presence and drafts

On a shared topic, the live margin shows who is in the room and who is typing.

**Draft a reply** reads only the record you can already see and saves a private writing on your desk. You still have to offer it. Be clear about what that means: the readable record — including what the other person has opened to you — is sent to the model endpoint you configure. Leave the key unset and the button does not exist; point it at a local model and nothing leaves the host.

```
XAI_API_KEY=xai-...
# or local Ollama:
# AGENT_API_KEY=ollama
# AGENT_BASE_URL=http://127.0.0.1:11434/v1
# AGENT_MODEL=llama3.2
```

## What this is not

Not a public forum. Not a live shared doc. It is a quiet desk for two, with a door between them that opens only when both are ready.
