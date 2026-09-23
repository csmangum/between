# Between — Design Document

**Status:** polished draft (UI + consent flows)
**Date:** 2026-09-23
**Implements:** the running app in this repo
**Audience:** the two people who will use it, and anyone who extends it

---

## 1. What this is for

Two people need a durable place to think in writing — topics, long pieces, comments, and a running chat — without the default of modern collaboration software: everything is visible the moment it is typed.

The problem is not “how do we share.” Sharing is easy. The problem is:

- How does a thought stay *mine* until I am ready?
- How does the other person receive it as an act, not as leakage?
- How does the record of what was shared remain collectible later?

Between is a two-person room with a door. The door is closed by default. It opens only when one person offers and the other agrees.

That is the whole product.

---

## 2. What this is not

Not Slack. Not a forum. Not Google Docs. Not a diary app with a share link. Not a CRDT that merges two keyboards into one page.

Those tools optimize for simultaneous presence. Between optimizes for **asymmetric readiness**. One person may be finished; the other may not be ready to see it. The software should not collapse that difference.

It is also not a group product. The cardinality is two. Adding a third person would change the ethics, the UI, and the data model. That is a different app.

---

## 3. Design principles

1. **Private is the zero state.** A new topic, writing, or comment is visible only to its author. Absence of a share action is not an oversight; it is the default.
2. **Seeing is an agreement, not a side effect of hosting.** Putting the app on a machine the other person can reach must not imply they can read the pages.
3. **Offer and accept are distinct moves.** Offering says “I am willing.” Accepting says “I will receive.” Neither move should be inferred from the other.
4. **Grain is small.** Sharing a topic does not share every writing inside it. Sharing a writing does not share later comments. Consent does not cascade unless we explicitly decide it should.
5. **Reversal is first-class.** Pulling something back is as important as offering it. The UI treats revoke as a normal act, not an admin override.
6. **The record is the point.** Once something has been agreed into the shared table, it belongs in the archive. The app exists so the conversation can be kept.
7. **Local copies are the author’s.** Each person’s private markdown lives under their own directory. The shared directory is written only after accept.
8. **Chat is the shared layer, not the draft layer.** Real-time talk assumes a room both people have already agreed to enter.

---

## 4. Metaphor

Three surfaces, not one:

| Surface | Name in the UI | Meaning |
|---|---|---|
| Author-only | **Desk** | You write here. The other person cannot see the page. |
| Pending | **Sealed offer** | They know something exists. They do not have the body. |
| Mutual | **Shared table** | Both may read. Chat may open. Archive may keep it. |

The metaphor is letters, not a whiteboard. A whiteboard is already in the room. A letter has to be sent, and then opened.

Titles of offered items are visible. Bodies are not. A title is the envelope label — enough to decide whether to open, not enough to have read.

---

## 5. People and trust

Exactly two accounts, defined by the host’s environment:

- `USER1_*` and `USER2_*` names, display names, passwords
- No signup, no invites, no roles beyond “the other person”
- Session cookie signed with `SECRET_KEY`

Trust assumptions for v1:

- Both people are known to each other.
- The operator of the host machine can read the SQLite file. Hosting on a machine only one person controls is a real privacy fact, not a footnote.
- The other person is not an attacker; they are a counterpart. The consent model protects against *premature seeing*, not against a hostile co-user with shell access.

If the two people do not trust the same host, v1 is the wrong architecture. See §12 and §16.

---

## 6. Object model

Four kinds of object.

### Topic

A container. Has a title, an optional opening note (`prompt`), and a creator.

A topic is the unit of *room*. Chat belongs to a topic. Writings and comments live in a topic.

Creating a topic does not notify the other person and does not list it on their home.

### Writing

A long-form piece inside a topic. Markdown. Has its own title and its own share state.

A writing may exist in a shared topic and still be private. That is intentional: the room can be open while a draft on the desk is not.

### Comment

A short note on a writing, or on the topic itself. Same share state as writings.

Comments are not threaded beyond an optional `parent_id` reserved for later. v1 treats them as a flat list per writing / per topic.

### Chat message

A line in the topic’s live margin. No private state. If the topic is not shared, the socket is closed and the log is not shown to the counterpart. If the topic is shared, every chat line is part of the mutual record.

Chat is the one object that does not go through offer/accept. The topic’s agreement *is* the agreement to talk.

---

## 7. Consent protocol

Every Topic, Writing, and Comment has:

```
share_status:  private | offered | shared
offered_at
accepted_at
```

### State machine

```
          offer                accept
private ---------> offered ---------> shared
   ^                  |                  |
   |      decline     |                  |
   +------------------+                  |
   ^                                     |
   |              revoke / pull back     |
   +-------------------------------------+
```

- **offer** — only the author (for a topic: the creator)
- **accept / decline** — only the other person, and only from `offered`
- **revoke** — only the author, from `offered` or `shared`, returns to `private`

Decline and revoke both land on `private` so the author can offer again later. We do not keep a `declined` state in v1. That is a product choice: refusal is quiet, not a permanent mark.

### Independence of grains

```
Topic shared  ─┬─ writing private     → counterpart does not see the writing at all
               ├─ writing offered     → counterpart sees title + Agree
               └─ writing shared      → counterpart sees body
```

A writing cannot be usefully offered unless its topic is already shared. The UI says so. Otherwise the counterpart would be asked to accept a page inside a room they have not entered.

Comments follow the same rule relative to the topic (and, for writing-level comments, the writing should already be readable).

### What “visible” vs “open” means

The code splits two predicates:

- **visible** — the person may know the object exists (author, or status is `offered` or `shared`)
- **open** — the person may read the body (author, or status is `shared`)

Private objects are neither visible nor open to the counterpart.
Offered objects are visible, not open.
Shared objects are both.

Home listings use visibility. Bodies, archive, and export use openness.

---

## 8. Visibility matrix

For person B, looking at an object authored by A:

| Object | private | offered | shared |
|---|---|---|---|
| Topic on B’s home | absent | “Waiting for your agreement” | on the shared table |
| Topic prompt / writings list | inaccessible (redirect home) | consent page only | full topic page |
| Writing body | absent from B’s topic page | title + sealed card | rendered Markdown |
| Comment body | absent, or sealed if offered | sealed + Agree | shown |
| Chat | closed | closed | live + history |
| Archive / export | omitted | omitted | included |

Author A always sees their own bodies, including after offer and after share.

---

## 9. Interface

### Home

Three bands:

1. **Waiting for your agreement** — topics the counterpart offered. No prompt text.
2. **Private to you** — topics you created, any status. Your desk.
3. **Shared table** — topics in `shared`.

Creating a topic is labeled “Keep on my desk,” not “Create” or “Publish.”

### Topic page (author)

- Badge: private / offered / shared
- Actions: Offer to *Name* / Pull it back / Make private again
- Each writing has the same badge and actions
- Compose box: “Save to my desk”
- Chat panel locked until the topic is shared

### Topic page (counterpart, offered topic)

A dedicated **consent page**. Title only. Two buttons: Agree and open / Decline.

No preview of the prompt. No writing count that would leak how much was prepared. (v1 still lets the author see counts on their own desk; the counterpart does not get a dossier.)

### Topic page (shared)

Both people see the prompt. Writings appear according to each writing’s own state. Chat is live.

### Archive

The readable record: author’s own pages plus anything in `shared`. Export Markdown and JSON use the same filter. Sealed offers are not in the export. That is deliberate — an unread letter is not yet part of the collected conversation.

---

## 10. Storage

v1 is **single-host, local disk**, not two replicas.

```
data/
  between.db                 SQLite source of truth
  local/<username>/...       markdown mirror of that person's objects
  shared/...                 markdown written only on accept
```

SQLite is what the app queries. The markdown tree is for humans and backups: you can open your desk in a text editor. It is not a second authority and it is not synced to the counterpart’s laptop.

Implications, stated plainly:

- Whoever runs the process can read `between.db`.
- `data/local/a/` is not cryptographically closed to `b` if `b` has filesystem access.
- “Stored locally” in v1 means “stored on this machine, namespaced by author, not shown in the other person’s UI until accept.”
- It does **not** yet mean “the bytes never leave my device.”

The markdown split (`local/` vs `shared/`) exists so the intended boundary is visible on disk, not only in SQL. If we later encrypt per-user, those directories are the natural unit.

---

## 11. Runtime

- FastAPI + Jinja + one SQLite file
- Session cookie auth, two hard-coded people
- WebSocket `/ws/topics/{id}` for chat, accepted only when the topic is `shared`
- Docker Compose for hosting; `run.sh` for a laptop
- No third-party services, no analytics, no email

This stack is a choice: one process a person can read, run, and back up. It is not a platform.

---

## 12. Security notes

In scope for v1:

- No public registration
- Bodies withheld from the counterpart’s HTTP responses until `shared`
- Export filtered the same way as the archive
- Markdown sanitized on render (bleach)
- Session secret required in production

Out of scope for v1, and therefore not promised:

- E2E encryption between the two people
- Protection against the host operator
- Protection against someone with the other password
- Audit log of every offer/accept that survives revoke
- Fine-grained edit history

Revoke hides the body from future reads. It does not un-read a body already seen, and it does not wipe a shared markdown file the counterpart may have already exported. Consent is about access now, not about erasing memory. The UI should not pretend otherwise.

---

## 13. Flows

### A writes, B never knows

A creates topic → A writes → files land in `data/local/a/` → B’s home unchanged → B hitting `/topics/{id}` redirects home.

### A offers a topic, B declines

A offers → B sees a sealed card → B declines → topic returns to private → B’s home forgets it. A still has every word.

### A offers a topic, B accepts, A keeps a draft back

Topic becomes shared → chat unlocks → A’s existing writings stay private → B does not see them → A offers one writing → B accepts that writing only.

This is the important flow. The room can be mutual while the work remains staged.

### A revokes after B has read

Status returns to private. B loses the body on the next load. B may still have an export from earlier. The app does not chase that copy.

---

## 14. Current implementation map

| Concern | Where |
|---|---|
| People | `app/auth.py` |
| Predicates visible / open | `app/access.py` |
| Share status + UI labels | `app/share.py` |
| Tables | `app/models.py` |
| Markdown mirrors | `app/store.py` |
| Routes, export, flashes | `app/main.py` |
| Chat presence hub | `app/hub.py` |
| Schema add-ons | `app/db.py` `migrate()` |
| Desk / consent / topic UI | `app/templates/` |
| Chat client | `app/static/chat.js` |

If a change touches “who can see this,” it belongs in `access.py` first, then the template. Do not sprinkle status checks only in Jinja.

---

## 15. Non-goals (v1)

- More than two users
- Real-time co-editing of a writing
- Read receipts
- Mobile native apps
- Federation or email bridges
- An agent that can see a private desk that is not yours
- Public share links
- Reactions, votes, karma

Some of these may become later work. None of them should warp the consent model to get there.

---

## 16. Open questions

These are unresolved on purpose. The first draft should not fake answers.

**1. Does an edit to a shared writing need a new accept?**
Today, edit-in-place is not built. When it is, two honest options: (a) edits flow into the already-shared object, or (b) an edit reverts the writing to `offered`. (a) is how letters get postscripts. (b) is how you prevent bait-and-switch. We should pick with the two users, not by defaulting to “sync.”

**2. What does revoke mean after reading?**
Access ends. Memory does not. Should the archive keep a “was shared, then withdrawn” stub so the history of the relationship is honest? v1 just hides. That may be too clean.

**3. Should offered titles be blankable?**
A title can leak. An option to offer as “A writing” with no title would be stricter.

**4. True local-first.**
The honest next architecture is: each person runs an instance (or a folder of markdown), and offers are encrypted packets the other instance imports on accept. That removes the host-operator caveat. It also means conflict, clock, and delivery design we do not have.

**5. Chat after revoke of a topic.**
If a topic returns to private, the chat history is still in SQLite. Who may export it? v1: only while the topic is shared, export includes chat. After revoke, counterpart export drops it. Author still has the db. This is inconsistent in spirit with “the record is the point.” Needs a rule.

**6. Notifications.**
Without them, an offer can sit unseen. With them, the app starts to feel like a messenger. Maybe a single quiet badge on home is enough.

**7. Third surfaces.**
A future “read together” mode — both looking at a shared writing, commenting in the margin in real time — should still not punch a hole in private desks.

---

## 17. Roadmap

**Now (this draft, running)**
Two users, three grains of consent, local markdown mirrors, shared chat after topic accept with presence and typing, archive/export of readable objects, an optional agent that drafts a private reply from the readable record only.

**Next, still small**
- Edit writings with an explicit rule from question 1
- Withdrawal stub in the archive
- Offer without a revealing title
- Basic “you have N sealed offers” on home (already partly there)
- Backup script for `data/`

**Later, only if the two people need it**
- Per-user encryption at rest
- Two-instance sync of offer packets
- Optional agent that may read *only* `data/shared/` and only when invited onto a topic

The later items are not the identity of the app. The identity is the door.

---

## 18. How to judge whether the design is working

Use it for a real topic both people care about.

It is working if:

- You can write for a week without the other person knowing the topic exists
- Offering feels like sending, not like flipping a permission checkbox
- Accepting feels like opening, not like joining a channel
- After a few exchanges, the archive is a document you would actually re-read
- Revoking something does not feel like deleting the other person

It is failing if:

- People paste drafts into another app because they do not trust the desk
- Everything gets offered immediately “so we can talk”
- The shared table becomes a junk drawer of half-accepted fragments
- The host machine is treated as if it were E2E private

The last failure is on the design doc, not the user. This draft should stay honest about v1’s host.

---

## 19. One-sentence spec

Between is a two-person writing room where every page begins on the author’s desk, becomes readable only after an offer and an accept, and is collected into a shared archive only once that agreement has happened.
