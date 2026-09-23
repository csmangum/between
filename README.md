# Between

A private two-person discussion space. Each person writes locally. The other person cannot read a page until both have agreed.

The first-draft design document is [DESIGN.md](DESIGN.md).

## How access works

Everything starts **private**.

1. You write a topic, a long piece, or a comment. It is stored on this machine under `data/local/<your-name>/`.
2. You **offer** it. The other person sees that something was offered — not the body.
3. They **agree**. Only then does the body become readable, and a copy is written to `data/shared/`.
4. You can **pull it back**. Shared access ends.

Chat on a topic stays closed until the *topic itself* is offered and accepted. Chat is the shared layer after agreement.

## Quick start (local)

```bash
cp .env.example .env
# edit the two accounts and SECRET_KEY
chmod +x run.sh
./run.sh
```

Open `http://127.0.0.1:8000`.

## Host it (Docker)

```bash
cp .env.example .env
docker compose up -d --build
```

Put Caddy or nginx in front with HTTPS if it will sit on the public internet.

## Accounts

Only two people exist, set in `.env`:

| Variable | Meaning |
|---|---|
| `USER1_NAME` / `USER2_NAME` | login name |
| `USER1_DISPLAY` / `USER2_DISPLAY` | name shown in the UI |
| `USER1_PASSWORD` / `USER2_PASSWORD` | password |
| `SECRET_KEY` | signs the session cookie |

## Where the files live

```
data/
  between.db              # local SQLite
  local/<user>/...        # each person's private markdown
  shared/...              # copies written only after agreement
```

Archive and export only include what *you* are allowed to read: your private pages plus anything both of you accepted.

## Presence and drafts

On a shared topic, the live margin shows who is in the room and who is typing.

**Draft a reply** reads only the record you can already see and saves a private writing on your desk. You still have to offer it.

```
XAI_API_KEY=xai-...
# or local Ollama:
# AGENT_API_KEY=ollama
# AGENT_BASE_URL=http://127.0.0.1:11434/v1
# AGENT_MODEL=llama3.2
```

## What this is not

Not a public forum. Not a live shared doc. It is a desk for two people, with a door between them that only opens by agreement.
