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
    if (typeof msg.html === "string") body.innerHTML = msg.html;
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
