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

Two people share the room, set in `.env`. An optional admin login can act as either of them. It is not a third person, and it does not exist until you set it.

| Variable | Meaning |
|---|---|
| `USER1_NAME` / `USER2_NAME` | login name |
| `USER1_DISPLAY` / `USER2_DISPLAY` | name shown in the UI |
| `USER1_PASSWORD_HASH` / `USER2_PASSWORD_HASH` | scrypt hash from `python -m app.auth` (preferred) |
| `USER1_PASSWORD` / `USER2_PASSWORD` | plaintext alternative, hashed in memory at start |
| `ADMIN_PASSWORD_HASH` / `ADMIN_PASSWORD` | optional. When set, the name `admin` can sign in and switch between the two people. Off when both are unset. |
| `SECRET_KEY` | signs the session cookie; at least 32 random characters |

The app refuses to start with a sample `SECRET_KEY` or sample passwords. Changing a person's password signs that person out everywhere. Changing the admin secret signs the admin session out and leaves the two people signed in. An admin session keeps working if only one person's password changes.

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

## Writing

Pages are Markdown. The desk gives you a toolbar and shortcuts (Ctrl/⌘+B, I, K), continues lists and quotes when you press Enter, indents a list with Tab, counts words, and can show the page as it will be kept while you type — the preview is drawn by the server with the same renderer and nothing is stored. **Just the page** hides the rest of the room; Esc brings it back. Everything works as a plain textarea when scripts are off.

Beyond the usual Markdown, the room renders straight quotes and dashes as typographer's quotes and dashes, footnotes (`[^1]` … `[^1]: the note`), definition lists (a term, then a line starting with `: `), and abbreviations (`*[HTML]: HyperText Markup Language`). Images are reduced to their alt text; links open in a new tab.

Archive and export only include what *you* are allowed to read: your private pages plus anything both of you accepted. If something you had opened is returned to the other person's desk, you keep a line — its title, when it was opened, when it was returned — never the page. Nothing is kept for an offer that was never opened.

## Returning to it

**Find**, on the Kept page, searches every topic, page, note and margin line you may read. The first letters of a word are enough. It answers with the same rule as the archive — nothing sealed, nothing on the other desk — and never hints that a hidden page matched. Each hit opens the topic's reading view at the exact place.

**Read it through**, linked from every topic you can open, lays the topic out as a document: pages and notes in the order they were written, a contents list, a word count and reading time, the margin at the end while the topic is shared. Nothing opens from there; sealed things are counted and pointed back to the topic page. It prints cleanly.

## What the other person can see before opening

The title of a sealed topic or writing, a count of sealed offers waiting, and — inside a shared topic — that you are in the margin and typing. Nothing about anything private, not even that it exists.

## The room itself

Two palettes: the dark room and paper. The app follows your system by default; the round button in the top bar (and on the door) pins a choice in that browser. Nothing about the choice leaves your machine.

Writing fields grow with the words. Under every Markdown field there is a small toolbar: **Write / Preview** (the preview is drawn by the server with the same renderer the page uses, and nothing is stored), a running word count with a reading time, and **Write in full**, which gives the page the whole screen until you press Esc.

A half-written page is kept in your own browser, for your own name, and picked up again when you come back. It is forgotten the moment you save it, or when you press Leave. It is never sent anywhere.

Each writing shows how long it asks of its reader. A topic with several writings gets a short contents list. Soft times like "yesterday · 14:10" carry the exact moment on hover.

## Presence and drafts

On a shared topic, the live margin shows who is in the room and who is typing. Lines from the same person a few minutes apart sit together under one name. If you have scrolled up when something arrives, a small pill points to it; if the tab is in the background, its title counts what came in.

The only notification is the **Waiting** badge in the header. It updates on its own, and the page you are looking at says in one line when the other person offers, opens, leaves unopened or returns something there. The margin never notifies.

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
