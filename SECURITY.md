# Security and privacy review — Between

**Date:** 2026-09-25
**Scope:** the whole repository at the time of review: FastAPI app, templates, static JS, storage layer, Docker/compose, `run.sh`, dependencies.
**Method:** manual code review of every route and template against the consent model in [DESIGN.md](DESIGN.md) §7–§8 and §12, dependency audit with `pip-audit`, and a live check of the running server's behaviour. Every finding marked *fixed* has a regression test in `tests/test_security.py`.

---

## 1. Threat model

Between is a two-person writing room. Its privacy promise is narrow and specific: **a page is unreadable to the other person until the author offers it and the other person opens it.**

Who we defend against, in order of how much this code can do about it:

| Adversary | What they want | Posture |
|---|---|---|
| Anyone on the network who is not one of the two people | read pages, log in, make one person's browser act for them | **Primary.** Everything in §3 targets this. |
| The other person, acting through the web app | see a private or sealed body early, learn how much was drafted | **Primary.** Enforced in `app/access.py` and every route; see §3.4. |
| A third-party service | learn who uses the app, when, from where | **Primary.** The app now makes no third-party requests to render a page (§3.5). The optional drafting agent is the one deliberate exception and is labelled as such. |
| Another OS user on the same host | read `data/` or the SQLite file | **Reduced.** Files and directories are owner-only (§3.6). |
| The host operator (root, hypervisor, backups) | read everything | **Out of scope.** Stated honestly in DESIGN.md §5, §10, §12. The fix is the two-instance / per-user encryption design in DESIGN.md §16, not a patch. |
| Someone holding the other person's password | act as them | **Out of scope** beyond throttling and hashed storage. Two accounts, no MFA. |

---

## 2. Summary of findings

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| 1 | **High** | dependencies | `starlette 0.46.2` (pinned by `fastapi 0.115`) had 14 published advisories | fixed — FastAPI 0.141.1, Starlette 1.7.0 pinned; `pip-audit` clean |
| 2 | **High** | auth | `SECRET_KEY` fell back to `dev-only-change-me`; anyone knowing the default could mint a session cookie for either person | fixed — startup refuses placeholder or <32-char keys |
| 3 | **High** | auth | Default passwords `change-me` were accepted; `run.sh` copied `.env.example` and *started serving* with them | fixed — refuses placeholders and <12 chars; `run.sh` stops until passwords are set |
| 4 | **High** | deploy | Docker published `0.0.0.0:8000` and `run.sh` listened on `0.0.0.0` — plain HTTP on every interface, cookie without `Secure` | fixed — loopback by default; `HTTPS_ONLY` default on in compose |
| 5 | **Medium** | auth | No brute-force protection on `/login` | fixed — sliding-window throttle per address (5/15 min) and per name (20/15 min), 429 |
| 6 | **Medium** | CSRF | Only `SameSite=lax` stood between a hostile page and every state-changing POST; WebSocket handshake accepted any `Origin` | fixed — `Origin`/`Referer` must match `Host` for POST and WebSocket; `SameSite=strict` |
| 7 | **Medium** | privacy | Every page loaded fonts from `fonts.googleapis.com` / `fonts.gstatic.com`, telling Google each visit and IP | fixed — system font stack, zero third-party requests |
| 8 | **Medium** | headers | No CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, HSTS; private pages were cacheable by the browser | fixed — strict CSP without inline script, frame denial, `no-referrer`, `no-store`, HSTS under HTTPS |
| 9 | **Medium** | access | `/writings/{id}/…` and `/comments/{id}/…` redirected to `/topics/{topic_id}#writing-{id}` even when the caller was not allowed to know the object existed — an oracle for the other person's private topic ids and writing counts | fixed — invisible objects behave exactly like missing ids |
| 10 | **Medium** | privacy | Pulling back a topic/writing/comment left its copy in `data/shared/` | fixed — shared mirror pruned on revoke; re-opening a topic re-mirrors its readable pages |
| 11 | **Medium** | storage | `data/`, markdown mirrors, and the SQLite/WAL files were created world-readable (umask default) | fixed — umask `077`, directories `0700`, files `0600` |
| 12 | **Medium** | deploy | Container ran as root with a writable root filesystem | fixed — unprivileged user, read-only rootfs, `no-new-privileges`, all capabilities dropped |
| 13 | **Low** | auth | Plaintext password compare leaked the secret's length through an early return | fixed — SHA-256 both sides, then `compare_digest` |
| 14 | **Low** | auth | Passwords could only be stored in plaintext in the environment | improved — optional `USERn_PASSWORD_HASH` (PBKDF2-SHA256, 600k iterations), `python -m app.passwords` |
| 15 | **Low** | input | `parent_id` on comments was not validated: a foreign id produced a 500 (an existence oracle) | fixed — must be a readable comment on the same topic/writing |
| 16 | **Low** | ws | A non-object JSON frame or invalid JSON crashed the handler and left a stale seat in the presence hub | fixed — malformed frames ignored; seat always released |
| 17 | **Low** | XSS | Markdown authors could set `target`/`rel` on links via raw HTML; `rel="noopener"` was only added when absent | fixed — attributes stripped by bleach, always set by us; explicit protocol allow-list |
| 18 | **Low** | DoS | No request body cap; unauthenticated POSTs were fully buffered before auth ran | fixed — 2 MB cap (`MAX_REQUEST_KB`), 413 |
| 19 | **Low** | disclosure | `/docs`, `/redoc`, `/openapi.json` served the full route map unauthenticated; `Server: uvicorn` header | fixed — schema endpoints off, `--no-server-header` |
| 20 | **Low** | session | No explicit lifetime; not cleared on login | fixed — `SESSION_DAYS` (default 7); session reset on login |
| 21 | **Info** | privacy | "Draft a reply" sends the readable record — including the *other person's* opened words — to a third-party model endpoint. The UI said only that private pages are safe. | disclosed — copy on the topic page and in `.env.example` now says so; see §4 |
| 22 | **Info** | design | Host operator can read everything; revoke does not un-read | accepted, documented (DESIGN.md §5, §10, §12) |

---

## 3. What the code now enforces

### 3.1 Configuration must be safe before the process serves a request
`app/config.py`, `app/auth.py`. `SECRET_KEY` must be present, not a known placeholder, and at least 32 characters. Each person needs either a `*_PASSWORD` of at least 12 characters (placeholders rejected) or a `*_PASSWORD_HASH`. Usernames are `[a-z0-9][a-z0-9_-]{0,39}` because they name directories under `data/local/`. The two names must differ. Violations raise `ConfigError` and uvicorn exits.

### 3.2 Authentication
`app/auth.py`, `app/passwords.py`, `app/throttle.py`, `POST /login`. Hashed passwords use PBKDF2-SHA256 from the standard library (no new dependency). Plaintext passwords are compared in constant time independent of length. Failed logins are counted per client address and per account name in a sliding window; a blocked attempt gets 429 without checking the password. Success clears the counters and resets the session before storing the user.

### 3.3 Sessions and cross-site requests
`app/main.py`, `app/security.py`. The cookie is `HttpOnly`, `SameSite=Strict`, `Secure` when `HTTPS_ONLY`, with a `Max-Age` of `SESSION_DAYS`. Independently of cookies, `OriginCheckMiddleware` refuses any `POST/PUT/PATCH/DELETE` and any WebSocket handshake whose `Origin` (or `Referer` fallback) is not this host. Browsers always send `Origin` on cross-site form posts and WebSocket handshakes, so this stops CSRF and cross-site WebSocket hijacking without per-form tokens.

### 3.4 Authorization
`app/access.py` is still the single source of truth (`*_visible` / `*_open`). The review confirmed every route consults it or an equivalent ownership check. The change in this round is uniformity of *failure*: a writing or comment the caller may not know about now redirects to `/` exactly like a nonexistent id, instead of to a URL containing the parent topic id. The WebSocket re-checks `topic_open` on every message so a revoke ends the room immediately.

### 3.5 Browser-side hardening
`SecurityHeadersMiddleware` sets `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' ws://host wss://host; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'`, plus `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP, `Cache-Control: no-store` on pages, and HSTS when `HTTPS_ONLY`. The one inline script (chat config) became data attributes so the CSP needs no `unsafe-inline`. Google Fonts were removed: rendering a page no longer contacts anyone but this host. Markdown is rendered through bleach with an explicit tag/attribute/protocol allow-list, no `img`, and external links always carry `rel="noopener noreferrer" target="_blank"`.

### 3.6 Storage
`app/store.py`, `app/db.py`. The process sets `umask 077`; directories under `data/` are `0700`, files `0600`, including the SQLite database and its WAL/SHM companions. Revoking removes the page from `data/shared/`; re-opening a topic re-mirrors its currently shared writings and comments so the on-disk boundary matches the database.

### 3.7 Deployment
Container runs as uid 10001 with a read-only root filesystem, `/tmp` on tmpfs, `no-new-privileges`, and no capabilities. Compose binds `127.0.0.1:8000` only — a TLS reverse proxy on the same host is the intended front door — and honours its `X-Forwarded-*` headers so the login throttle sees real client addresses. `run.sh` listens on loopback unless `HOST` is set, never auto-reloads unless `DEV=1`, and refuses to start until real passwords exist.

---

## 4. Residual risks and recommendations

These are not bugs in the code; they are the limits of the v1 architecture and the operator's choices.

1. **Host operator sees everything.** SQLite and the markdown mirrors are plaintext. If the two people do not both trust the host, the fix is architectural (DESIGN.md §16.4), not a setting. Full-disk encryption on the host and restricted shell access are the practical mitigations today.
2. **The drafting agent is a deliberate privacy exception.** When one person asks for a draft, the topic's readable record — including the other person's *opened* writings, comments and chat — is sent to `AGENT_BASE_URL`. That is consent to read, not consent to forward. Options, in increasing strictness: leave the key unset (the button then does not appear); point `AGENT_BASE_URL` at a local model (Ollama) so nothing leaves the host; or agree between the two people before enabling it. The UI copy now states what is sent.
3. **Login throttle is in-process.** It resets on restart and is per worker; run a single uvicorn worker (the default) or accept a proportionally larger budget. Behind a proxy, `FORWARDED_ALLOW_IPS` must be set (compose does this) or every visitor shares one address bucket.
4. **Revoke does not un-read.** Exports and browser memory the other person already has are theirs. `Cache-Control: no-store` reduces what lingers on a shared computer; it cannot recall a downloaded `export.md`.
5. **Chat and offered titles are not sealed.** Titles of sealed items are visible by design (the envelope label). Chat has no private state (DESIGN.md §6). Anyone who does not want that should not use titles or chat for sensitive content.
6. **No audit trail.** There is no durable log of offer/accept/revoke events (DESIGN.md §12). Adding one changes the product; it is listed as an open question, not slipped in here.
7. **Backups.** `data/` now has restrictive modes; a backup that copies it elsewhere inherits none of that unless the backup tool preserves modes and the destination is equally private.
8. **Dependencies drift.** `pip-audit` is clean today. Run it (or Dependabot, which the repository already uses) on each dependency bump.

---

## 5. Operating checklist

- [ ] `SECRET_KEY` is random and at least 32 characters (`run.sh` generates one).
- [ ] Both people have `*_PASSWORD_HASH` rather than plaintext, generated with `python -m app.passwords`.
- [ ] `HTTPS_ONLY=true` and the app sits behind Caddy/nginx with TLS; port 8000 is bound to loopback only.
- [ ] `ALLOWED_HOSTS` is set to the public hostname.
- [ ] `FORWARDED_ALLOW_IPS` matches the proxy (compose sets `*`, which is safe only because the port is loopback-bound).
- [ ] The drafting agent is either disabled or the two people have agreed to it, and `AGENT_BASE_URL` is a host both accept.
- [ ] `data/` (or the named volume) is included in backups that preserve permissions and are themselves private.
- [ ] `pip-audit -r requirements.txt` is clean after each dependency change.
