(() => {
  const config = window.BetweenChat;
  if (!config) return;

  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-body");
  const presence = document.getElementById("presence");
  if (!log || !form || !input || !presence) return;

  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws = null;
  let typingTimer = null;
  let typingOn = false;
  let reconnectAttempt = 0;
  let closedOnPurpose = false;
  const pending = [];

  function addBubble(msg) {
    const el = document.createElement("div");
    el.className = "bubble" + (msg.author === config.me ? " mine" : "");
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = `${msg.display} · ${msg.created_at}`;
    const body = document.createElement("div");
    body.textContent = msg.body;
    el.append(who, body);
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  }

  function showPresence(msg) {
    const names = (msg.here || []).map((p) => p.display);
    const here = names.length ? names.join(" · ") : "Empty room";
    const typing = (msg.typing || []).filter((name) => name && name !== config.display);
    presence.textContent = typing.length
      ? `${here} — ${typing.join(", ")} typing`
      : `${here} here`;
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

  function connect() {
    closedOnPurpose = false;
    setStatus(reconnectAttempt ? "Reconnecting…" : "Connecting…");
    ws = new WebSocket(`${proto}://${location.host}/ws/topics/${config.topicId}`);

    ws.addEventListener("open", () => {
      reconnectAttempt = 0;
      setStatus("Connected");
      flushPending();
    });

    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "presence") showPresence(msg);
      else if (msg.body) addBubble(msg);
    });

    ws.addEventListener("close", () => {
      if (closedOnPurpose) return;
      const delay = Math.min(10000, 500 * Math.pow(2, reconnectAttempt++));
      setStatus("Disconnected — retrying…");
      setTimeout(connect, delay);
    });

    ws.addEventListener("error", () => {
      try {
        ws.close();
      } catch (_) {
        /* ignore */
      }
    });
  }

  input.addEventListener("input", () => {
    sendTyping(true);
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => sendTyping(false), 1200);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body) return;
    sendTyping(false);
    const payload = { type: "chat", body };
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify(payload));
    } else {
      pending.push(payload);
      setStatus("Queued — reconnecting…");
      if (!ws || ws.readyState > 1) connect();
    }
    input.value = "";
  });

  window.addEventListener("beforeunload", () => {
    closedOnPurpose = true;
    if (ws) ws.close();
  });

  log.scrollTop = log.scrollHeight;
  connect();
})();
