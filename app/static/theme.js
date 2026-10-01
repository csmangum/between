/* Runs before paint so the room never flashes the wrong theme. Kept tiny on purpose. */
(() => {
  let chosen = null;
  try {
    chosen = localStorage.getItem("between:theme");
  } catch (_) {
    /* storage may be unavailable; follow the system */
  }
  const system = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  const theme = chosen === "light" || chosen === "dark" ? chosen : system;
  document.documentElement.dataset.theme = theme;
})();
