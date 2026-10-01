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

  const desks = document.querySelectorAll("[data-desk]");
  if (!desks.length) return;

  window.addEventListener("beforeunload", (event) => {
    if (submitting) return;
    const dirty = [...desks].some((field) => {
      if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)) return false;
      return field.value !== field.defaultValue && field.value.trim() !== "";
    });
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
})();
