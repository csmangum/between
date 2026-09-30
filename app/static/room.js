(() => {
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
