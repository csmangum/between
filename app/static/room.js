(() => {
  let submitting = false;
  const THEME_KEY = "between:theme";
  const DRAFT_PREFIX = "between:draft:";
  const THEME_COLORS = { light: "#f3ecdf", dark: "#15120f" };

  document.querySelectorAll(".flash-dismiss").forEach((button) => {
    button.addEventListener("click", () => {
      const flash = button.closest(".flash");
      if (flash) flash.remove();
    });
  });

  /* Theme: the loader in <head> already set data-theme; here we let it be changed and followed. */
  const root = document.documentElement;
  const systemLight = window.matchMedia("(prefers-color-scheme: light)");
  const toggles = document.querySelectorAll("[data-theme-toggle]");

  function storedTheme() {
    try {
      const value = localStorage.getItem(THEME_KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (_) {
      return null;
    }
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", THEME_COLORS[theme] || THEME_COLORS.dark);
    const label = theme === "light" ? "Switch to the dark room" : "Switch to paper";
    toggles.forEach((button) => {
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
    });
  }

  applyTheme(root.dataset.theme === "light" ? "light" : "dark");

  toggles.forEach((button) => {
    button.addEventListener("click", () => {
      const next = root.dataset.theme === "light" ? "dark" : "light";
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (_) {
        /* fine: the choice lasts for this page only */
      }
      applyTheme(next);
    });
  });

  systemLight.addEventListener("change", (event) => {
    if (storedTheme()) return;
    applyTheme(event.matches ? "light" : "dark");
  });

  function forgetDrafts() {
    try {
      const doomed = [];
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (key && key.startsWith(DRAFT_PREFIX)) doomed.push(key);
      }
      doomed.forEach((key) => localStorage.removeItem(key));
    } catch (_) {
      /* nothing to forget */
    }
  }

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
      if (form.getAttribute("action") === "/logout") forgetDrafts();
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
