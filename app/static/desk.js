/* The writing surface: fields that grow, a count of words, a preview drawn by the server,
   drafts that survive a closed tab, and a way to write with nothing else on the page. */
(() => {
  const me = document.body.dataset.user || "";
  const DRAFT_PREFIX = "between:draft:";
  const WORDS_PER_MINUTE = 200;

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function toolButton(text, pressed) {
    const button = el("button", "tool");
    button.type = "button";
    button.textContent = text;
    if (pressed !== null) button.setAttribute("aria-pressed", pressed ? "true" : "false");
    return button;
  }

  /* Fields that grow with the words */

  function grow(field) {
    if (!field.isConnected || field.hidden || field.offsetParent === null) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight + 2}px`;
  }

  function growAll(scope) {
    scope.querySelectorAll("textarea[data-desk]").forEach(grow);
  }

  /* Drafts: kept in this browser only, for this person only, forgotten on save or on leaving */

  function draftKey(form, field) {
    return `${DRAFT_PREFIX}${me}:${location.pathname}:${form.dataset.keep}:${field.name}`;
  }

  function readDraft(key) {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }

  function writeDraft(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (_) {
      /* no storage, no draft */
    }
  }

  function draftFields(form) {
    return [...form.querySelectorAll("[data-desk]")].filter(
      (field) => (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) && field.name,
    );
  }

  function restoreDrafts(form) {
    const fields = draftFields(form);
    const restored = [];
    fields.forEach((field) => {
      const saved = readDraft(draftKey(form, field));
      if (saved !== null && saved !== field.defaultValue && field.value === field.defaultValue) {
        field.value = saved;
        restored.push(field);
      }
      let timer = null;
      field.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          const value = field.value;
          writeDraft(draftKey(form, field), value === field.defaultValue ? null : value);
        }, 250);
      });
    });
    if (!restored.length) return;

    const fold = form.closest("details");
    if (fold) fold.open = true;

    const note = el("p", "draft-note");
    const text = document.createElement("span");
    text.textContent = "Picked up what you were writing here.";
    const discard = el("button");
    discard.type = "button";
    discard.textContent = "Start over";
    discard.addEventListener("click", () => {
      fields.forEach((field) => {
        field.value = field.defaultValue;
        writeDraft(draftKey(form, field), null);
        field.dispatchEvent(new Event("input", { bubbles: true }));
      });
      note.remove();
    });
    note.append(text, discard);
    restored[restored.length - 1].insertAdjacentElement("afterend", note);
  }

  if (me) {
    document.querySelectorAll("form[data-keep]").forEach(restoreDrafts);
    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (event.defaultPrevented || !(form instanceof HTMLFormElement) || !form.dataset.keep) return;
      draftFields(form).forEach((field) => writeDraft(draftKey(form, field), null));
    });
  }

  /* The count under a page */

  function countWords(text) {
    const trimmed = text.trim();
    return trimmed ? trimmed.split(/\s+/).length : 0;
  }

  function describe(text) {
    const words = countWords(text);
    if (!words) return "nothing yet";
    const label = `${words} ${words === 1 ? "word" : "words"}`;
    if (words < WORDS_PER_MINUTE) return `${label} · under a minute`;
    return `${label} · ${Math.round(words / WORDS_PER_MINUTE)} min read`;
  }

  /* Preview: the server draws it with the same renderer the page uses */

  async function fetchPreview(text) {
    const response = await fetch("/preview", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ body: text }),
      credentials: "same-origin",
    });
    if (!response.ok || response.redirected) throw new Error("preview unavailable");
    return response.text();
  }

  /* Writing in full: the form moves into a page of its own and back again */

  let focusRoom = null;
  let focusPlaceholder = null;
  let focusForm = null;

  function leaveFocus() {
    if (!focusForm || !focusPlaceholder) return;
    focusPlaceholder.replaceWith(focusForm);
    const form = focusForm;
    focusForm = null;
    focusPlaceholder = null;
    growAll(form);
    const body = form.querySelector('textarea[name="body"]');
    if (body) body.focus({ preventScroll: true });
    form.scrollIntoView({ block: "center" });
  }

  function ensureFocusRoom() {
    if (focusRoom) return focusRoom;
    focusRoom = el("dialog", "focus-room");
    focusRoom.setAttribute("aria-label", "Writing in full");
    const inner = el("div", "focus-inner");
    const bar = el("div", "focus-bar");
    const label = el("span", "meta");
    label.textContent = "Writing in full · Esc returns to the page";
    const back = el("button", "ghost");
    back.type = "button";
    back.textContent = "Back to the page";
    back.addEventListener("click", () => focusRoom.close());
    bar.append(label, back);
    inner.append(bar);
    focusRoom.append(inner);
    focusRoom.addEventListener("close", leaveFocus);
    document.body.append(focusRoom);
    return focusRoom;
  }

  function enterFocus(form) {
    if (typeof HTMLDialogElement === "undefined") return;
    const room = ensureFocusRoom();
    focusForm = form;
    focusPlaceholder = document.createComment("writing in full");
    form.replaceWith(focusPlaceholder);
    room.querySelector(".focus-inner").append(form);
    room.showModal();
    growAll(form);
    const body = form.querySelector('textarea[name="body"]');
    if (body) body.focus();
  }

  /* Tools under every Markdown field */

  function buildTools(form) {
    const body = form.querySelector('textarea[name="body"][data-desk]');
    if (!body) return;

    const tools = el("div", "writing-tools");
    const group = el("div", "tool-group");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Write or preview");
    const writeButton = toolButton("Write", true);
    const previewButton = toolButton("Preview", false);
    group.append(writeButton, previewButton);

    const count = el("span", "word-count");
    count.setAttribute("aria-live", "polite");
    count.textContent = describe(body.value);

    const full = toolButton("Write in full", null);
    full.classList.add("solo");

    tools.append(group, count, full);
    const pane = el("div", "preview-pane prose");
    pane.hidden = true;
    body.insertAdjacentElement("afterend", tools);
    tools.insertAdjacentElement("afterend", pane);

    let previewing = false;
    let request = 0;

    function setMode(preview) {
      previewing = preview;
      writeButton.setAttribute("aria-pressed", preview ? "false" : "true");
      previewButton.setAttribute("aria-pressed", preview ? "true" : "false");
      body.hidden = preview;
      pane.hidden = !preview;
      if (!preview) {
        grow(body);
        body.focus({ preventScroll: true });
      }
    }

    async function showPreview() {
      const text = body.value;
      setMode(true);
      if (!text.trim()) {
        pane.innerHTML = "";
        const empty = el("p", "empty");
        empty.textContent = "Nothing to preview yet.";
        pane.append(empty);
        return;
      }
      const ticket = ++request;
      pane.setAttribute("aria-busy", "true");
      try {
        const html = await fetchPreview(text);
        if (ticket !== request) return;
        pane.innerHTML = html;
      } catch (_) {
        if (ticket !== request) return;
        pane.innerHTML = "";
        const failed = el("p", "empty");
        failed.textContent = "The preview could not be drawn. Your words are still in the field.";
        pane.append(failed);
      } finally {
        pane.removeAttribute("aria-busy");
      }
    }

    body.addEventListener("input", () => {
      count.textContent = describe(body.value);
    });
    writeButton.addEventListener("click", () => setMode(false));
    previewButton.addEventListener("click", showPreview);
    full.addEventListener("click", () => enterFocus(form));
    form.addEventListener(
      "click",
      (event) => {
        if (previewing && event.target instanceof Element && event.target.closest('button[type="submit"]')) {
          setMode(false);
        }
      },
      true,
    );
  }

  document.querySelectorAll("form[data-writing]").forEach(buildTools);

  /* Growth last, after drafts may have filled a field */

  document.querySelectorAll("textarea[data-desk]").forEach((field) => {
    grow(field);
    field.addEventListener("input", () => grow(field));
  });
  document.querySelectorAll("details").forEach((fold) => {
    fold.addEventListener("toggle", () => {
      if (fold.open) growAll(fold);
    });
  });
})();
