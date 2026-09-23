(() => {
  const config = window.BetweenChat;
  if (!config) return;

  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-body");
  const presence = document.getElementById("presence");
  if (!log || !form || !input || !presence) return;

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/topics/${config.topicId}`);
  let typingTimer = null;
  let typingOn = false;

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

  function sendTyping(on) {
    if (ws.readyState !== 1 || typingOn === on) return;
    typingOn = on;
    ws.send(JSON.stringify({ type: "typing", on }));
  }

  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "presence") showPresence(msg);
    else if (msg.body) addBubble(msg);
  });

  input.addEventListener("input", () => {
    sendTyping(true);
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => sendTyping(false), 1200);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = input.value.trim();
    if (!body || ws.readyState !== 1) return;
    sendTyping(false);
    ws.send(JSON.stringify({ type: "chat", body }));
    input.value = "";
  });

  log.scrollTop = log.scrollHeight;
})();
