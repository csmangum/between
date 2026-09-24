(() => {
  const config = window.BetweenChat;
  if (!config) return;

  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-body");
  const presence = document.getElementById("presence");
  const limit = document.getElementById("chat-limit");
  if (!log || !form || !input || !presence) return;

  const maxLength = 4000;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws = null;
  let typingTimer = null;
  let typingOn = false;
  let reconnectAttempt = 0;
  let closedOnPurpose = false;
  const pending = [];
  const pendingBubbles = [];
  let reconnectTimer = null;

  function joinNames(names) {
    if (names.length <= 1) return names[0] || "";
    if (names.length === 2) return `${names[0]} and ${names[1]}`;
    return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  }

  function addBubble(msg, pendingMessage) {
    const el = document.createElement("div");
    el.className = "bubble" + (msg.author === config.me ? " mine" : "");
    if (pendingMessage) el.classList.add("pending");
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = `${msg.display} · ${msg.created_at}`;
    const body = document.createElement("div");
    body.textContent = msg.body;
    el.append(who, body);
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    if (pendingMessage) pendingBubbles.push({ body: msg.body, el });
  }

  function settlePending(msg) {
    if (msg.author !== config.me) return false;
    const index = pendingBubbles.findIndex((item) => item.body === msg.body);
    if (index === -1) return false;
    const item = pendingBubbles.splice(index, 1)[0];
    item.el.classList.remove("pending");
    const who = item.el.querySelector(".who");
    if (who) who.textContent = `${msg.display} · ${msg.created_at}`;
    return true;
  }

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

  log.scrollTop = log.scrollHeight;
  connect();
})();
