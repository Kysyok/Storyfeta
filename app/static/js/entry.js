/* Home screen: one shared name box, then Create room or Join room.
   Signed-in players have no name box: the server uses their profile nickname. */
(function () {
  const { api, toast } = window.sf;
  const $ = (id) => document.getElementById(id);
  const nameInput = $("nickname"); // null for signed-in players
  const cells = [...document.querySelectorAll(".code__cell")];
  const hostForm = $("host-form");
  const joinForm = $("join-form");

  function setError(form, message) {
    const el = form.querySelector(".form-error");
    el.textContent = message;
    el.hidden = !message;
  }

  /** The request body: the typed name for guests, nothing for signed-in players. */
  function nameBody(form) {
    if (!nameInput) return {};
    const name = nameInput.value.trim();
    if (!name) {
      setError(form, "Enter your name first.");
      nameInput.focus();
      return null;
    }
    return { nickname: name };
  }

  async function enter(form, path, body) {
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    setError(form, "");
    try {
      const data = await api(path, { method: "POST", body });
      window.location.assign(data.url);
    } catch (error) {
      setError(form, error.message + (nameInput ? "" : " You can change your name in Profile."));
      button.disabled = false;
    }
  }

  hostForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = nameBody(hostForm);
    if (body) enter(hostForm, "/api/rooms", body);
  });

  joinForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const code = cells.map((c) => c.value).join("").toUpperCase();
    if (code.length < cells.length) {
      setError(joinForm, `Enter all ${cells.length} characters of the room code.`);
      (cells.find((c) => !c.value) || cells[0]).focus();
      return;
    }
    const body = nameBody(joinForm);
    if (body) enter(joinForm, `/api/rooms/${encodeURIComponent(code)}/join`, body);
  });

  /* ---- room code cells: type, paste, backspace and arrows behave like one field ---- */
  const clean = (text) => text.toUpperCase().replace(/[^A-Z0-9]/g, "");

  cells.forEach((cell, i) => {
    cell.addEventListener("input", () => {
      const chars = clean(cell.value);
      cell.value = chars.slice(0, 1);
      // Several characters at once (autofill, some mobile keyboards): spread them out.
      for (let k = 1; k < chars.length && i + k < cells.length; k++) cells[i + k].value = chars[k];
      const next = Math.min(i + Math.max(chars.length, 1), cells.length - 1);
      if (chars.length) cells[next].focus();
    });
    cell.addEventListener("keydown", (event) => {
      if (event.key === "Backspace" && !cell.value && i > 0) { cells[i - 1].value = ""; cells[i - 1].focus(); }
      else if (event.key === "ArrowLeft" && i > 0) cells[i - 1].focus();
      else if (event.key === "ArrowRight" && i < cells.length - 1) cells[i + 1].focus();
    });
    cell.addEventListener("paste", (event) => {
      event.preventDefault();
      const chars = clean((event.clipboardData || window.clipboardData).getData("text"));
      chars.slice(0, cells.length - i).split("").forEach((ch, k) => { cells[i + k].value = ch; });
      cells[Math.min(i + chars.length, cells.length - 1)].focus();
    });
    cell.addEventListener("focus", () => cell.select());
  });
})();
