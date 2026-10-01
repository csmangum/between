(() => {
  // --- Times: the server renders a fallback; here it becomes the viewer's own clock. ---------
  const MINUTE = 60 * 1000;
  const HOUR = 60 * MINUTE;
  const DAY = 24 * HOUR;

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function clock(date) {
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function shortDate(date, withYear) {
    const options = { month: "short", day: "2-digit" };
    if (withYear) options.year = "numeric";
    return date.toLocaleDateString(undefined, options);
  }

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function formatExact(date) {
    return `${shortDate(date, true)} · ${clock(date)}`;
  }

  // Mirrors fmt_dt_soft in app/views.py so the first paint and the re-render agree.
  function formatSoft(date, now) {
    const seconds = Math.floor((now - date) / 1000);
    if (seconds < 45) return "just now";
    if (seconds < 90) return "a minute ago";
    if (seconds < 45 * 60) return `${Math.max(2, Math.floor(seconds / 60))} minutes ago`;
    if (seconds < 90 * 60) return "an hour ago";
    if (sameDay(date, now)) return `today · ${clock(date)}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (sameDay(date, yesterday)) return `yesterday · ${clock(date)}`;
    if (now - date < 7 * DAY) return `${date.toLocaleDateString(undefined, { weekday: "long" })} · ${clock(date)}`;
    return shortDate(date, true);
  }

  function renderTime(el, now) {
    const date = new Date(el.getAttribute("datetime"));
    if (Number.isNaN(date.getTime())) return;
    el.textContent = el.dataset.when === "soft" ? formatSoft(date, now) : formatExact(date);
    el.title = formatExact(date);
  }

  function localizeTimes(root) {
    const now = new Date();
    (root || document).querySelectorAll("time[datetime]").forEach((el) => renderTime(el, now));
  }

  function timeElement(iso, mode) {
    const el = document.createElement("time");
    el.setAttribute("datetime", iso);
    el.dataset.when = mode || "soft";
    renderTime(el, new Date());
    return el;
  }

  window.Between = Object.assign(window.Between || {}, { localizeTimes, timeElement, formatSoft, formatExact });
  localizeTimes();
  setInterval(() => {
    const now = new Date();
    document.querySelectorAll('time[data-when="soft"]').forEach((el) => renderTime(el, now));
  }, 30 * 1000);

  // --- Small courtesies ------------------------------------------------------------------------
  let submitting = false;

  document.querySelectorAll(".flash-dismiss").forEach((button) => {
    button.addEventListener("click", () => {
      const flash = button.closest(".flash");
      if (flash) flash.remove();
    });
  });

  document.addEventListener("keydown", (event) => {
    const field = event.target;
    if (!(field instanceof HTMLTextAreaElement) || !field.hasAttribute("data-desk")) return;
    if (event.key !== "Enter" || !(event.metaKey || event.ctrlKey) || event.isComposing) return;
    event.preventDefault();
    if (field.form) field.form.requestSubmit();
  });

  // --- One quiet stream: the badge stays true, and the page you are on says when it changed. ---
  const me = document.body.dataset.me;
  if (me && "EventSource" in window) {
    const other = document.body.dataset.other || "They";
    const NOTICES = {
      sealed: `${other} sent something here.`,
      opened: `${other} opened what you sent.`,
      unopened: `${other} left it unopened for now.`,
      returned: `${other} returned something to their desk.`,
    };

    const setSealed = (count) => {
      const nav = document.querySelector(".nav");
      if (!nav) return;
      let badge = nav.querySelector(".nav-badge");
      if (!count) {
        if (badge) badge.remove();
        return;
      }
      if (!badge) {
        badge = document.createElement("a");
        badge.className = "nav-badge";
        badge.href = "/#incoming";
        badge.append("Waiting ");
        const number = document.createElement("span");
        number.className = "count";
        badge.append(number);
        const desk = nav.querySelector('a[href="/"]');
        if (desk) desk.insertAdjacentElement("afterend", badge);
        else nav.prepend(badge);
      }
      badge.querySelector(".count").textContent = String(count);
    };

    const notice = (text) => {
      const main = document.getElementById("content");
      if (!main) return;
      let line = main.querySelector(".room-notice");
      if (!line) {
        line = document.createElement("p");
        line.className = "room-notice";
        line.setAttribute("role", "status");
        main.prepend(line);
      }
      line.textContent = "";
      line.append(`${text} `);
      const see = document.createElement("button");
      see.type = "button";
      see.className = "link";
      see.textContent = "See it";
      see.addEventListener("click", () => window.location.reload());
      line.append(see);
    };

    const here = window.location.pathname.match(/^\/topics\/(\d+)/);
    const source = new EventSource("/events");
    source.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (typeof payload.sealed === "number") setSealed(payload.sealed);
      if (payload.type === "close") {
        source.close();
        return;
      }
      if (payload.type !== "topic") return;
      if (here && Number(here[1]) === payload.topic) notice(NOTICES[payload.what] || "Something changed here.");
      else if (window.location.pathname === "/" && payload.what === "sealed") notice("Something new is waiting for you.");
    });
  }

  // --- Drafts: what is typed on the desk stays in this browser until the server has kept it. --
  const DRAFT_PREFIX = "between:draft:v1:";
  const DRAFT_TTL = 30 * DAY;
  const desks = [...document.querySelectorAll("[data-desk]")].filter(
    (field) => (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) && field.id,
  );

  function draftKey(field) {
    return `${DRAFT_PREFIX}${location.pathname}#${field.id}`;
  }

  function readDraft(key) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const draft = JSON.parse(raw);
      return draft && typeof draft.v === "string" ? draft : null;
    } catch (_) {
      return null;
    }
  }

  let remembers = false;

  function writeDraft(key, draft) {
    try {
      localStorage.setItem(key, JSON.stringify(draft));
      remembers = true;
    } catch (_) {
      remembers = false; // storage full or disabled: the desk still works, it just does not remember
    }
  }

  function dropDraft(key) {
    try {
      localStorage.removeItem(key);
    } catch (_) {
      /* ignore */
    }
  }

  function allDraftKeys() {
    const keys = [];
    try {
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (key && key.startsWith(DRAFT_PREFIX)) keys.push(key);
      }
    } catch (_) {
      /* ignore */
    }
    return keys;
  }

  function saveDraft(field) {
    const key = draftKey(field);
    if (field.value === field.defaultValue || field.value.trim() === "") {
      dropDraft(key);
      return;
    }
    writeDraft(key, { v: field.value, t: Date.now(), s: false });
  }

  function restoreNote(field, key) {
    const note = document.createElement("p");
    note.className = "draft-note";
    note.append("Restored what you were writing here, from this browser. ");
    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "link";
    discard.textContent = "Discard it";
    discard.addEventListener("click", () => {
      field.value = field.defaultValue;
      dropDraft(key);
      note.remove();
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.focus();
    });
    note.append(discard);
    field.insertAdjacentElement("afterend", note);
    field.addEventListener("input", () => note.remove(), { once: true });
  }

  function housekeep() {
    // A success flash means the form just submitted was kept; its draft has done its job.
    const kept = Boolean(document.querySelector(".flash.ok"));
    const now = Date.now();
    allDraftKeys().forEach((key) => {
      const draft = readDraft(key);
      if (!draft || now - (draft.t || 0) > DRAFT_TTL || (kept && draft.s)) dropDraft(key);
    });
  }

  function restoreDrafts() {
    desks.forEach((field) => {
      const key = draftKey(field);
      const draft = readDraft(key);
      if (!draft) return;
      if (field.value !== field.defaultValue || draft.v === field.defaultValue) {
        dropDraft(key);
        return;
      }
      field.value = draft.v;
      writeDraft(key, { ...draft, s: false });
      const fold = field.closest("details");
      if (fold) fold.open = true;
      restoreNote(field, key);
    });
  }

  const saveTimers = new Map();
  desks.forEach((field) => {
    field.addEventListener("input", () => {
      clearTimeout(saveTimers.get(field));
      saveTimers.set(field, setTimeout(() => saveDraft(field), 250));
    });
  });

  document.addEventListener(
    "submit",
    (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.id === "chat-form") return;
      if (form.dataset.sent === "1") {
        event.preventDefault();
        return;
      }
      if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        return;
      }
      submitting = true;
      form.dataset.sent = "1";
      form.querySelectorAll("button").forEach((button) => {
        if (button.type === "submit" || button.type === "") button.disabled = true;
      });
      // Mark rather than drop: if the server sends us to the login page instead, the words are still here.
      desks
        .filter((field) => field.form === form)
        .forEach((field) => {
          clearTimeout(saveTimers.get(field));
          const key = draftKey(field);
          if (field.value === field.defaultValue || field.value.trim() === "") dropDraft(key);
          else writeDraft(key, { v: field.value, t: Date.now(), s: true });
        });
    },
    false,
  );

  window.addEventListener("pageshow", () => {
    submitting = false;
    document.querySelectorAll("form[data-sent]").forEach((form) => {
      delete form.dataset.sent;
      form.querySelectorAll("button").forEach((button) => {
        button.disabled = false;
      });
    });
  });

  housekeep();
  restoreDrafts();

  if (!desks.length) return;

  // Only when the browser cannot remember the draft does leaving the page risk the words.
  window.addEventListener("beforeunload", (event) => {
    if (submitting || remembers) return;
    const dirty = desks.some((field) => field.value !== field.defaultValue && field.value.trim() !== "");
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
