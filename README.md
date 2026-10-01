# Between

A private room for two. Write until you are ready. Nothing crosses until you send it and they choose to open it.

The first-draft design document is [DESIGN.md](DESIGN.md).

## How access works

Everything starts **private**.

1. You write a topic, a long piece, or a comment. It is stored on this machine under `data/local/<your-name>/`.
2. You **send** it. The other person sees that something was offered — not the body.
3. They **open** it. Only then does the body become readable, and a copy is written to `data/shared/`.
4. You can **pull it back**. Shared access ends, the shared copy is removed from disk, and everything inside the topic returns to the desk of whoever wrote it.

While something is on your desk you can change it or remove it. A private topic's title and opening note can be edited, and a private writing or note can be removed (with confirmation). Anything you have sent has to be pulled back first.

Chat on a topic stays closed until the *topic itself* is sent and opened. The margin is for after agreement.

Your words stay yours. If they pull a topic back that you wrote in, you keep your own pages in it (listed on your desk as *begun by them · your pages only*), and they cannot delete a topic while it holds anything you wrote.

## Quick start (local)

```bash
chmod +x run.sh
./run.sh            # first run creates .env with a fresh SECRET_KEY and stops
# edit .env: the two names and passwords (see Accounts)
./run.sh
```

Open `http://127.0.0.1:8000`. The app listens on loopback only; set `HOST=0.0.0.0` to change that, and only behind HTTPS.

## Host it (Docker)

```bash
cp .env.example .env && chmod 600 .env
# edit .env
docker compose up -d --build
```

The container runs as uid 1000 on a read-only filesystem. If `./data` already exists from an older run, `sudo chown -R 1000:1000 data` once.

To put it on a small Google Cloud VM with HTTPS, follow [deploy/gcp/RUNBOOK.md](deploy/gcp/RUNBOOK.md).

## Accounts

Only two people exist, set in `.env`:

| Variable | Meaning |
|---|---|
| `USER1_NAME` / `USER2_NAME` | login name |
| `USER1_DISPLAY` / `USER2_DISPLAY` | name shown in the UI |
| `USER1_PASSWORD_HASH` / `USER2_PASSWORD_HASH` | scrypt hash from `python -m app.auth` (preferred) |
| `USER1_PASSWORD` / `USER2_PASSWORD` | plaintext alternative, hashed in memory at start |
| `SECRET_KEY` | signs the session cookie; at least 32 random characters |

The app refuses to start with a sample `SECRET_KEY` or sample passwords. Changing a person's password signs that person out everywhere.

Login is throttled after three misses. Sessions last seven idle days, thirty at most.

## Where the files live

```
data/
  between.db              # SQLite — the record
  local/<user>/...        # each person's private markdown (mirror)
  shared/...              # copies written after agreement, removed on pull-back
```

The markdown mirrors are a convenience so the boundary is visible on disk. `MARKDOWN_MIRROR=false` keeps everything in SQLite only.

What you are still typing is kept in your own browser's local storage until the server confirms it was saved, so a dropped connection or an expired session does not lose the page. It never leaves the device, and it is cleared once the words are on your desk.

Times are shown on your clock, not the server's. Exports are labeled UTC.

Archive and export only include what *you* are allowed to read: your private pages plus anything both of you accepted.

## What the other person can see before opening

The title of a sealed topic or writing, a count of sealed offers waiting, and — inside a shared topic — that you are in the margin and typing. Nothing about anything private, not even that it exists.

## Presence and drafts

On a shared topic, the live margin shows who is in the room and who is typing.

**Draft a reply** is optional and off by default. It needs a model key in `.env` **and** both people to allow it from their desk. When someone asks for a draft, what that person can already read on the topic — including the other person's opened pages and recent margin lines — is sent to the configured provider. Private pages never are. Either person can withdraw at any time.

```
XAI_API_KEY=xai-...
# or local Ollama, which keeps the record on your own machine:
# AGENT_API_KEY=ollama
# AGENT_BASE_URL=http://127.0.0.1:11434/v1
# AGENT_MODEL=llama3.2
```

## Tests and checks

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

The same four commands run in CI on every pull request (`.github/workflows/ci.yml`). Configuration lives in `pyproject.toml`.

## What this is not

Not a public forum. Not a live shared doc. It is a quiet desk for two, with a door between them that opens only when both are ready.
