/* Shared helpers: toast, tiny DOM builder, JSON API client, data-endpoint forms. */
(function () {
  const sf = (window.sf = window.sf || {});

  let toastTimer;
  sf.toast = function (message, ms = 3500) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, ms);
  };

  /** h("div", {class: "x"}, "text", childNode) -> element. Text is never parsed as HTML. */
  sf.h = function (tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === false || value == null) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  };

  /** Fetch JSON. Throws Error(message) using the server's {code, message} when present. */
  sf.api = async function (path, { method = "GET", body } = {}) {
    const response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 204) return null;
    let data = null;
    try { data = await response.json(); } catch (_) { /* no body */ }
    if (!response.ok) {
      const message = data && data.message
        ? data.message
        : response.status === 422 ? "Check the form and try again." : "Something went wrong. Try again.";
      throw new Error(message);
    }
    return data;
  };

  sf.showFormError = function (form, message) {
    const el = form.querySelector(".form-error");
    if (!el) return sf.toast(message);
    el.textContent = message;
    el.hidden = !message;
  };

  // <form data-endpoint="/api/..."> posts its fields as JSON, then follows the returned url.
  document.querySelectorAll("form[data-endpoint]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      sf.showFormError(form, "");
      try {
        const data = await sf.api(form.dataset.endpoint, {
          method: "POST",
          body: Object.fromEntries(new FormData(form)),
        });
        window.location.assign(data.url);
      } catch (error) {
        sf.showFormError(form, error.message);
        button.disabled = false;
        // A signed-in player's automatic nickname was refused (say, taken in this room):
        // show the field so they can pick another name for this game only.
        const field = form.querySelector("[data-nickname-field][hidden]");
        if (field) {
          field.hidden = false;
          const input = field.querySelector("input");
          input.required = true;
          input.focus();
        }
      }
    });
  });
})();
