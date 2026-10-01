(() => {
  // The desk: a Markdown writing surface built on the textarea that is already there.
  // `data-editor="writing"` gets the full set; `data-editor="note"` gets typography, auto-grow,
  // shortcuts and smart lists. Without scripts the textarea still works as it always did.

  const LIST_LINE = /^(\s*)((?:>\s?)*)(?:([-*+])\s+|(\d+)([.)])\s+)?(.*)$/;
  const WORDS_PER_MINUTE = 220;
  const PREVIEW_DELAY = 350;
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform);
  const MOD = MAC ? "⌘" : "Ctrl";

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === false || value == null) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : value);
    });
    children.forEach((child) => child && node.append(child));
    return node;
  }

  function wordCount(text) {
    const words = text.trim().split(/\s+/).filter(Boolean).length;
    return words;
  }

  function countLabel(text) {
    const words = wordCount(text);
    if (!words) return "Nothing yet";
    const minutes = Math.max(1, Math.round(words / WORDS_PER_MINUTE));
    return `${words} ${words === 1 ? "word" : "words"} · about ${minutes} min to read`;
  }

  class Desk {
    constructor(textarea) {
      this.ta = textarea;
      this.kind = textarea.dataset.editor === "writing" ? "writing" : "note";
      this.previewOn = false;
      this.focusOn = false;
      this.previewTimer = null;
      this.previewAbort = null;
      this.build();
      this.bind();
      this.fit();
      this.count();
    }

    // --- structure ---------------------------------------------------------------------------
    build() {
      const ta = this.ta;
      this.root = el("div", { class: `desk-editor is-${this.kind}` });
      ta.parentNode.insertBefore(this.root, ta);

      if (this.kind === "writing") {
        this.tools = el("div", { class: "desk-tools", role: "toolbar", "aria-label": "Formatting" });
        const tool = (label, title, action, extra) => {
          const button = el("button", { type: "button", class: "tool", title, text: label, ...(extra || {}) });
          button.addEventListener("mousedown", (event) => event.preventDefault());
          button.addEventListener("click", () => {
            action();
          });
          this.tools.append(button);
          return button;
        };
        tool("Bold", `Bold · ${MOD}+B`, () => this.wrap("**"));
        tool("Italic", `Italic · ${MOD}+I`, () => this.wrap("*"));
        tool("Heading", "Heading · cycles ## and ###", () => this.heading());
        tool("Quote", "Quote the selected lines", () => this.prefixLines("> "));
        tool("List", "Bulleted list", () => this.prefixLines("- "));
        tool("Numbered", "Numbered list", () => this.prefixLines("1. "));
        tool("Link", `Link · ${MOD}+K`, () => this.link());
        tool("Code", "Inline code, or a block when lines are selected", () => this.code());
        tool("Rule", "A quiet break between parts", () => this.rule());
        tool("Footnote", "Add a footnote at the end of the page", () => this.footnote());
        this.tools.append(el("span", { class: "tool-gap" }));
        this.previewButton = tool("Preview", "See the page as it will be kept", () => this.togglePreview(), {
          "aria-pressed": "false",
        });
        this.focusButton = tool("Just the page", "Hide everything else · Esc to come back", () => this.toggleFocus(), {
          "aria-pressed": "false",
        });
        this.root.append(this.tools);
      }

      this.pane = el("div", { class: "desk-pane" });
      this.pane.append(ta);
      if (this.kind === "writing") {
        this.preview = el("div", { class: "desk-preview prose", hidden: true, "aria-live": "polite" });
        this.pane.append(this.preview);
      }
      this.root.append(this.pane);

      if (this.kind === "writing") {
        this.counter = el("span", { class: "desk-count" });
        const hint = el("span", {
          class: "desk-hint",
          text: `Markdown. ${MOD}+B bold · ${MOD}+I italic · ${MOD}+K link · ${MOD}+Enter saves`,
        });
        this.foot = el("div", { class: "desk-foot" }, this.counter, hint);
        this.root.append(this.foot);
      }

      ta.classList.add("desk-field");
      ta.setAttribute("spellcheck", "true");
      ta.style.overflow = "hidden";
      ta.style.resize = "none";
    }

    bind() {
      const ta = this.ta;
      ta.addEventListener("input", () => {
        this.fit();
        this.count();
        if (this.previewOn) this.schedulePreview();
      });
      ta.addEventListener("keydown", (event) => this.onKey(event));
      const fold = ta.closest("details");
      if (fold) fold.addEventListener("toggle", () => this.fit());
      window.addEventListener("resize", () => this.fit());
      if (this.kind === "writing") {
        document.addEventListener("keydown", (event) => {
          if (event.key === "Escape" && this.focusOn) {
            event.preventDefault();
            this.toggleFocus(false);
          }
        });
      }
    }

    // --- shape -------------------------------------------------------------------------------
    fit() {
      const ta = this.ta;
      if (ta.offsetParent === null) return; // inside a closed <details>; measured when it opens
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight + 2}px`;
    }

    count() {
      if (this.counter) this.counter.textContent = countLabel(this.ta.value);
    }

    // --- editing primitives ------------------------------------------------------------------
    selection() {
      return { start: this.ta.selectionStart, end: this.ta.selectionEnd, text: this.ta.value };
    }

    replace(start, end, replacement, selectStart, selectEnd) {
      const ta = this.ta;
      ta.focus();
      ta.setRangeText(replacement, start, end, "preserve");
      ta.setSelectionRange(selectStart, selectEnd);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    }

    wrap(marker) {
      const { start, end, text } = this.selection();
      const before = text.slice(start - marker.length, start);
      const after = text.slice(end, end + marker.length);
      if (before === marker && after === marker) {
        const inner = text.slice(start, end);
        this.replace(start - marker.length, end + marker.length, inner, start - marker.length, end - marker.length);
        return;
      }
      const selected = text.slice(start, end);
      if (selected.startsWith(marker) && selected.endsWith(marker) && selected.length >= marker.length * 2) {
        const inner = selected.slice(marker.length, selected.length - marker.length);
        this.replace(start, end, inner, start, start + inner.length);
        return;
      }
      this.replace(start, end, `${marker}${selected}${marker}`, start + marker.length, end + marker.length);
    }

    lineBounds(start, end) {
      const text = this.ta.value;
      const from = text.lastIndexOf("\n", start - 1) + 1;
      let to = text.indexOf("\n", end);
      if (to === -1) to = text.length;
      return { from, to };
    }

    prefixLines(prefix) {
      const { start, end, text } = this.selection();
      const { from, to } = this.lineBounds(start, end);
      const lines = text.slice(from, to).split("\n");
      const numbered = /^\d+\. $/.test(prefix);
      const stripper = numbered ? /^(\s*)\d+[.)]\s+/ : new RegExp(`^(\\s*)${prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);
      const allPrefixed = lines.every((line) => stripper.test(line) || line.trim() === "");
      const changed = lines.map((line, index) => {
        if (allPrefixed) return line.replace(stripper, "$1");
        if (line.trim() === "" && lines.length > 1) return line;
        const indent = line.match(/^\s*/)[0];
        const rest = line.slice(indent.length).replace(/^(?:[-*+]\s+|\d+[.)]\s+|>\s?)/, "");
        return `${indent}${numbered ? `${index + 1}. ` : prefix}${rest}`;
      });
      const joined = changed.join("\n");
      this.replace(from, to, joined, from, from + joined.length);
    }

    heading() {
      const { start, end, text } = this.selection();
      const { from, to } = this.lineBounds(start, start);
      const line = text.slice(from, to);
      let next;
      if (line.startsWith("### ")) next = line.slice(4);
      else if (line.startsWith("## ")) next = `### ${line.slice(3)}`;
      else if (/^#+\s/.test(line)) next = `## ${line.replace(/^#+\s/, "")}`;
      else next = `## ${line}`;
      const shift = next.length - line.length;
      this.replace(from, to, next, Math.max(from, start + shift), Math.max(from, end + shift));
    }

    link() {
      const { start, end, text } = this.selection();
      const selected = text.slice(start, end);
      if (/^https?:\/\//.test(selected)) {
        const out = `[text](${selected})`;
        this.replace(start, end, out, start + 1, start + 5);
        return;
      }
      const label = selected || "text";
      const out = `[${label}](url)`;
      const urlStart = start + label.length + 3;
      this.replace(start, end, out, urlStart, urlStart + 3);
    }

    code() {
      const { start, end, text } = this.selection();
      const selected = text.slice(start, end);
      if (selected.includes("\n")) {
        const { from, to } = this.lineBounds(start, end);
        const block = `\`\`\`\n${text.slice(from, to)}\n\`\`\``;
        this.replace(from, to, block, from + 4, from + 4 + (to - from));
        return;
      }
      this.wrap("`");
    }

    rule() {
      const { start, end, text } = this.selection();
      const before = text.slice(0, start);
      const lead = before === "" ? "" : before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n";
      const insert = `${lead}---\n\n`;
      this.replace(start, end, insert, start + insert.length, start + insert.length);
    }

    footnote() {
      const { start, end, text } = this.selection();
      const used = [...text.matchAll(/\[\^(\d+)\]/g)].map((m) => Number(m[1]));
      const n = used.length ? Math.max(...used) + 1 : 1;
      const marker = `[^${n}]`;
      const tail = text.slice(end);
      const sep = text.trimEnd() === "" ? "" : "\n\n";
      const definition = `${sep}${marker}: `;
      // Two edits, one undo-able step each: the marker where the reader is, the note at the end.
      this.ta.setRangeText(marker, start, end, "end");
      const length = this.ta.value.length;
      const trailing = tail.length - tail.trimEnd().length;
      this.ta.setRangeText(definition, length - trailing, length, "end");
      const caret = this.ta.value.length;
      this.ta.focus();
      this.ta.setSelectionRange(caret, caret);
      this.ta.dispatchEvent(new Event("input", { bubbles: true }));
    }

    // --- keys ---------------------------------------------------------------------------------
    onKey(event) {
      if (event.isComposing) return;
      const mod = MAC ? event.metaKey : event.ctrlKey;
      if (mod && !event.shiftKey && !event.altKey) {
        const key = event.key.toLowerCase();
        if (key === "b") return this.shortcut(event, () => this.wrap("**"));
        if (key === "i") return this.shortcut(event, () => this.wrap("*"));
        if (key === "k") return this.shortcut(event, () => this.link());
      }
      if (event.key === "Enter" && !mod && !event.shiftKey && !event.altKey) return this.smartEnter(event);
      if (event.key === "Tab" && !mod && !event.altKey) return this.smartTab(event);
      return undefined;
    }

    shortcut(event, action) {
      event.preventDefault();
      action();
    }

    smartEnter(event) {
      const { start, end, text } = this.selection();
      if (start !== end) return;
      const { from } = this.lineBounds(start, start);
      const line = text.slice(from, start);
      const match = LIST_LINE.exec(line);
      if (!match) return;
      const [, indent, quotes, bullet, number, numberMark, rest] = match;
      const hasMarker = Boolean(bullet || number || quotes);
      if (!hasMarker) return;
      event.preventDefault();
      if (rest.trim() === "") {
        // An empty item: Enter ends the list (or the quote) instead of adding another marker.
        this.replace(from, start, indent, from + indent.length, from + indent.length);
        return;
      }
      let marker = "";
      if (bullet) marker = `${bullet} `;
      else if (number) marker = `${Number(number) + 1}${numberMark} `;
      const insert = `\n${indent}${quotes}${marker}`;
      this.replace(start, start, insert, start + insert.length, start + insert.length);
    }

    smartTab(event) {
      const { start, end, text } = this.selection();
      const { from, to } = this.lineBounds(start, end);
      const lines = text.slice(from, to).split("\n");
      const listy = lines.every((line) => {
        const m = LIST_LINE.exec(line);
        return m && (m[3] || m[4]);
      });
      if (!listy) return; // let Tab move focus, as it should in a form
      event.preventDefault();
      const changed = lines.map((line) => (event.shiftKey ? line.replace(/^ {1,2}/, "") : `  ${line}`));
      const joined = changed.join("\n");
      const delta = joined.length - (to - from);
      const single = lines.length === 1;
      const newStart = single ? Math.max(from, start + (event.shiftKey ? delta : 2)) : from;
      const newEnd = single ? Math.max(from, end + (event.shiftKey ? delta : 2)) : from + joined.length;
      this.replace(from, to, joined, newStart, newEnd);
    }

    // --- preview and focus -------------------------------------------------------------------
    togglePreview(force) {
      this.previewOn = typeof force === "boolean" ? force : !this.previewOn;
      this.previewButton.setAttribute("aria-pressed", String(this.previewOn));
      this.root.classList.toggle("has-preview", this.previewOn);
      this.preview.hidden = !this.previewOn;
      if (this.previewOn) this.renderPreview();
      this.fit();
    }

    schedulePreview() {
      clearTimeout(this.previewTimer);
      this.previewTimer = setTimeout(() => this.renderPreview(), PREVIEW_DELAY);
    }

    async renderPreview() {
      const body = this.ta.value;
      if (!body.trim()) {
        this.preview.innerHTML = "";
        this.preview.append(el("p", { class: "desk-preview-empty", text: "Nothing to show yet." }));
        return;
      }
      if (this.previewAbort) this.previewAbort.abort();
      this.previewAbort = new AbortController();
      const data = new FormData();
      data.set("body", body);
      try {
        const response = await fetch("/preview", {
          method: "POST",
          body: data,
          credentials: "same-origin",
          headers: { Accept: "text/html" },
          signal: this.previewAbort.signal,
        });
        if (!response.ok) throw new Error(String(response.status));
        // The server rendered and sanitized this exactly as the kept page will be.
        this.preview.innerHTML = await response.text();
        if (window.Between) window.Between.localizeTimes(this.preview);
      } catch (error) {
        if (error && error.name === "AbortError") return;
        this.preview.innerHTML = "";
        this.preview.append(el("p", { class: "desk-preview-empty", text: "The preview could not be drawn just now." }));
      }
    }

    toggleFocus(force) {
      this.focusOn = typeof force === "boolean" ? force : !this.focusOn;
      this.focusButton.setAttribute("aria-pressed", String(this.focusOn));
      this.focusButton.textContent = this.focusOn ? "Back to the room" : "Just the page";
      this.root.classList.toggle("is-focus", this.focusOn);
      document.body.classList.toggle("desk-focus", this.focusOn);
      this.fit();
      this.ta.focus();
      if (this.focusOn) this.root.scrollTop = 0;
    }
  }

  document.querySelectorAll("textarea[data-editor]").forEach((textarea) => new Desk(textarea));
})();
