(() => {
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-body");
  const presence = document.getElementById("presence");
  const limit = document.getElementById("chat-limit");
  const newPill = document.getElementById("chat-new");
  if (!log || !form || !input || !presence) return;
  const config = {
    topicId: log.dataset.topicId,
    me: log.dataset.me,
    display: log.dataset.display,
  };
  if (!config.topicId || !config.me) return;

  const maxLength = 4000;
  const groupWindowMs = 5 * 60 * 1000;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const baseTitle = document.title;
  let ws = null;
  let typingTimer = null;
  let typingOn = false;
  let reconnectAttempt = 0;
  let closedOnPurpose = false;
  const pending = [];
  const pendingBubbles = [];
  let reconnectTimer = null;
  let unseenBelow = 0;
  let unseenWhileAway = 0;

  function joinNames(names) {
    if (names.length <= 1) return names[0] || "";
    if (names.length === 2) return `${names[0]} and ${names[1]}`;
    return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  }

  /* Scroll position and what has not been seen yet */

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 48;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function showPill() {
    if (!newPill) return;
    newPill.textContent = unseenBelow === 1 ? "A new line below" : `${unseenBelow} new lines below`;
    newPill.hidden = false;
  }

  function hidePill() {
    unseenBelow = 0;
    if (newPill) newPill.hidden = true;
  }

  function markTitle() {
    document.title = unseenWhileAway ? `(${unseenWhileAway}) ${baseTitle}` : baseTitle;
  }

  log.addEventListener("scroll", () => {
    if (nearBottom()) hidePill();
  });
  if (newPill) {
    newPill.addEventListener("click", () => {
      log.scrollTo({ top: log.scrollHeight, behavior: "smooth" });
      hidePill();
    });
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      unseenWhileAway = 0;
      markTitle();
    }
  });

  /* Bubbles */

  function lastBubble() {
    const bubbles = log.querySelectorAll(".bubble");
    return bubbles.length ? bubbles[bubbles.length - 1] : null;
  }

  const SAFE_MARKDOWN_ELEMENTS = Object.fromEntries(
    [
      "a", "abbr", "blockquote", "br", "code", "dd", "div", "dl", "dt", "em", "h1", "h2", "h3", "h4",
      "hr", "li", "ol", "p", "pre", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "ul",
      "strong",
    ].map((tag) => [tag.toUpperCase(), () => document.createElement(tag)]),
  );
  const BLOCKED_MARKUP_TAGS = new Set(["IFRAME", "MATH", "OBJECT", "SCRIPT", "STYLE", "SVG", "TEMPLATE"]);

  function appendSafeMarkup(target, markup) {
    const parents = [target];
    let blockedTag = null;
    const decodeText = (text) => text.replace(/&(?:#(x[0-9a-f]+|[0-9]+)|(amp|lt|gt|quot|apos|nbsp));/gi, (entity, numeric, named) => {
      if (named) {
        return { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" }[named.toLowerCase()];
      }
      const point = numeric[0].toLowerCase() === "x" ? Number.parseInt(numeric.slice(1), 16) : Number.parseInt(numeric, 10);
      return point <= 0x10ffff ? String.fromCodePoint(point) : "\uFFFD";
    });
    const tokens = markup.match(/<[^>]*>|[^<]+|</g) || [];
    for (const token of tokens) {
      if (!token.startsWith("<") || token === "<") {
        if (!blockedTag) parents[parents.length - 1].append(document.createTextNode(decodeText(token)));
        continue;
      }
      const match = token.match(/^<\s*(\/?)\s*([a-z][a-z0-9]*)\b([^>]*)>/i);
      if (!match) continue;
      const [, closing, rawTag, attributes] = match;
      const tag = rawTag.toUpperCase();
      if (blockedTag) {
        if (tag === blockedTag && closing) blockedTag = null;
        continue;
      }
      if (BLOCKED_MARKUP_TAGS.has(tag) && !closing) {
        blockedTag = tag;
        continue;
      }
      if (closing) {
        const index = parents.map((parent) => parent.tagName).lastIndexOf(tag);
        if (index > 0) parents.length = index;
        continue;
      }
      const createElement = SAFE_MARKDOWN_ELEMENTS[tag];
      if (!createElement) continue;
      const copy = createElement();
      const attribute = (name) => {
        const found = attributes.match(new RegExp(`(?:^|\\s)${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, "i"));
        return found ? decodeText(found[1] ?? found[2] ?? found[3]) : null;
      };
      if (tag === "A") {
        const href = attribute("href");
        if (href) {
          try {
            const url = new URL(href, location.href);
            if (["http:", "https:", "mailto:"].includes(url.protocol)) {
              copy.setAttribute("href", url.href);
              if (url.protocol !== "mailto:" && url.origin !== location.origin) {
                copy.setAttribute("target", "_blank");
                copy.setAttribute("rel", "noopener noreferrer");
              }
            }
          } catch (_) {
            /* Ignore malformed links. */
          }
        }
        const title = attribute("title");
        if (title) copy.setAttribute("title", title);
      } else if (tag === "ABBR") {
        const title = attribute("title");
        if (title) copy.setAttribute("title", title);
      } else if (tag === "LI" || tag === "SUP") {
        const id = attribute("id");
        if (id && /^fn(ref)?:[A-Za-z0-9_.:-]+$/.test(id)) copy.id = id;
      }
      const classes = (attribute("class") || "").split(/\s+/);
      const allowedClasses = tag === "DIV" ? ["footnote"] : tag === "A" ? ["footnote-ref", "footnote-backref"] : [];
      classes.filter((name) => allowedClasses.includes(name)).forEach((name) => copy.classList.add(name));
      parents[parents.length - 1].append(copy);
      if (!["BR", "HR"].includes(tag) && !/\/\s*>$/.test(token)) parents.push(copy);
    }
  }

  function setTimestamp(element, msg) {
    element.textContent = "";
    element.append(`${msg.display} · `);
    if (window.Between && msg.created_at && !Number.isNaN(Date.parse(msg.created_at))) {
      element.append(window.Between.timeElement(msg.created_at, "soft"));
    } else {
      element.append(msg.created_at || "just now");
    }
  }

  function addBubble(msg, pendingMessage) {
    const mine = msg.author === config.me;
    const wasNearBottom = nearBottom();
    const previous = lastBubble();
    const now = msg.created_at_epoch_ms || Date.now();
    const el = document.createElement("div");
    el.className = "bubble" + (mine ? " mine" : "");
    el.dataset.author = msg.author;
    el.dataset.at = String(now);
    if (
      previous &&
      previous.dataset.author === msg.author &&
      now - Number(previous.dataset.at || 0) < groupWindowMs
    ) {
      el.classList.add("cont");
    }
    if (pendingMessage) el.classList.add("pending");
    const who = document.createElement("div");
    who.className = "who";
    setTimestamp(who, msg);
    const body = document.createElement("div");
    body.className = "prose compact";
    if (typeof msg.html === "string") appendSafeMarkup(body, msg.html);
    else body.textContent = msg.body;
    el.append(who, body);
    log.appendChild(el);
    if (pendingMessage) pendingBubbles.push({ body: msg.body, el });

    if (mine || wasNearBottom) {
      scrollToEnd();
    } else {
      unseenBelow += 1;
      showPill();
    }
    if (!mine && document.hidden) {
      unseenWhileAway += 1;
      markTitle();
    }
  }

  function settlePending(msg) {
    if (msg.author !== config.me) return false;
    const index = pendingBubbles.findIndex((item) => item.body === msg.body);
    if (index === -1) return false;
    const item = pendingBubbles.splice(index, 1)[0];
    item.el.classList.remove("pending");
    const serverEpoch = msg.created_at_epoch_ms;
    if (serverEpoch) {
      item.el.dataset.at = String(serverEpoch);
      item.el.classList.remove("cont");
      const previous = item.el.previousElementSibling;
      if (
        previous &&
        previous.classList.contains("bubble") &&
        previous.dataset.author === msg.author &&
        serverEpoch - Number(previous.dataset.at || 0) < groupWindowMs
      ) {
        item.el.classList.add("cont");
      }
    }
    const who = item.el.querySelector(".who");
    if (who) setTimestamp(who, msg);
    return true;
  }

  /* Presence */

  function showPresence(msg) {
    const here = (msg.here || []).map((person) => person.display).filter(Boolean);
    const typing = (msg.typing || []).filter((name) => name && name !== config.display);
    const others = here.filter((name) => name !== config.display);
    let line;
    if (!here.length) line = "The margin is empty";
    else if (!others.length) line = "Just you, for now";
    else line = `${joinNames(here)} ${here.length === 1 ? "is" : "are"} here`;
    if (typing.length === 1) line += ` · ${typing[0]} is writing`;
    else if (typing.length > 1) line += ` · ${joinNames(typing)} are writing`;
    presence.textContent = line;
  }

  function setStatus(text) {
    presence.textContent = text;
  }

  function sendTyping(on) {
    if (!ws || ws.readyState !== 1 || typingOn === on) return;
    typingOn = on;
    ws.send(JSON.stringify({ type: "typing", on }));
  }

  /* The line to the room */

  function flushPending() {
    while (pending.length && ws && ws.readyState === 1) {
      ws.send(JSON.stringify(pending.shift()));
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer !== null) return;
    const delay = Math.min(10000, 500 * Math.pow(2, reconnectAttempt++));
    setStatus("The line dropped. Trying again…");
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    closedOnPurpose = false;
    setStatus(reconnectAttempt ? "Finding the margin again…" : "Opening the margin…");
    ws = new WebSocket(`${proto}://${location.host}/ws/topics/${config.topicId}`);

    ws.addEventListener("open", () => {
      reconnectAttempt = 0;
      flushPending();
    });

    ws.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      if (msg.type === "presence") showPresence(msg);
      else if (msg.body && !settlePending(msg)) addBubble(msg, false);
    });

    ws.addEventListener("close", (event) => {
      if (closedOnPurpose) return;
      if (event.code === 4401 || event.code === 4404) {
        setStatus("The margin is closed");
        return;
      }
      scheduleReconnect();
    });

    ws.addEventListener("error", () => {
      try {
        ws.close();
      } catch (_) {
        /* ignore */
      }
    });
  }

  /* Composing */

  function updateLimit() {
    if (!limit) return;
    const left = maxLength - input.value.length;
    if (left > 400) {
      limit.hidden = true;
      return;
    }
    limit.hidden = false;
    limit.textContent = left >= 0 ? `${left} characters left in this line` : `${Math.abs(left)} over — it will be shortened`;
  }

  input.addEventListener("input", () => {
    updateLimit();
    sendTyping(input.value.trim().length > 0);
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => sendTyping(false), 1200);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = input.value.trim().slice(0, maxLength);
    if (!body) return;
    sendTyping(false);
    const payload = { type: "chat", body };
    addBubble(
      { author: config.me, display: config.display, created_at: "just now", body },
      true,
    );
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(payload));
    } else {
      pending.push(payload);
      setStatus("Held here until the line returns…");
    }
    input.value = "";
    updateLimit();
  });

  window.addEventListener("beforeunload", () => {
    closedOnPurpose = true;
    if (ws) ws.close();
  });

  scrollToEnd();
  connect();
})();
