/* Account page: sign in / register, saved defaults, archive list, deleting games and the account. */
(async function () {
  const { api, h, showFormError, toast } = window.sf;
  const $ = (id) => document.getElementById(id);

  async function load() {
    let me;
    try { me = await api("/api/me"); } catch (_) { $("auth-view").hidden = false; return; }
    $("me-view").hidden = false;
    $("me-email").textContent = `Signed in as ${me.email}.`;
    $("me-nickname").value = me.nickname;
    $("def-rounds").value = me.default_rounds;
    $("def-seconds").value = me.default_turn_seconds ?? 60;
    $("def-notimer").checked = me.default_turn_seconds === null;
    $("def-seconds").disabled = $("def-notimer").checked;

    const games = await api("/api/archive");
    $("archive-empty").hidden = games.length > 0;
    $("archive-list").replaceChildren(...games.map((g) => h(
      "li", { class: "archive__item" },
      h("div", null,
        h("a", { href: `/archive/${g.id}` }, g.title || `Room ${g.room_code}`),
        h("p", { class: "hint" }, `${new Date(g.finished_at).toLocaleString()}, ${g.players.join(", ")}`)),
      h("button", { class: "btn btn--secondary btn--small", type: "button", onclick: (e) => deleteGame(g, e.target) }, "Delete"),
    )));
  }

  async function deleteGame(game, button) {
    if (!window.confirm(`Delete "${game.title || `Room ${game.room_code}`}" from your history?`)) return;
    button.disabled = true;
    try {
      await api(`/api/archive/${game.id}`, { method: "DELETE" });
      const item = button.closest("li");
      item.remove();
      $("archive-empty").hidden = $("archive-list").children.length > 0;
      toast("Game deleted.");
    } catch (error) { toast(error.message); button.disabled = false; }
  }

  $("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode = event.submitter ? event.submitter.dataset.mode : "login";
    showFormError(event.target, "");
    const body = Object.fromEntries(new FormData(event.target));
    if (mode === "login" || !body.nickname.trim()) delete body.nickname;  // nickname only for new accounts
    try {
      await api(`/api/auth/${mode}`, { method: "POST", body });
      window.location.reload();
    } catch (error) { showFormError(event.target, error.message); }
  });

  $("nickname-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showFormError(event.target, "");
    try {
      const me = await api("/api/me/profile", { method: "PUT", body: { nickname: $("me-nickname").value } });
      $("me-nickname").value = me.nickname;
      toast("Nickname saved.");
    } catch (error) { showFormError(event.target, error.message); }
  });

  $("def-notimer").addEventListener("change", (e) => { $("def-seconds").disabled = e.target.checked; });
  $("defaults-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showFormError(event.target, "");
    try {
      await api("/api/me/defaults", { method: "PUT", body: {
        default_rounds: parseInt($("def-rounds").value, 10),
        default_turn_seconds: $("def-notimer").checked ? null : parseInt($("def-seconds").value, 10),
      } });
      toast("Defaults saved.");
    } catch (error) { showFormError(event.target, error.message); }
  });

  $("logout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    window.location.reload();
  });

  $("delete-account").addEventListener("click", async () => {
    if (!window.confirm("Delete your account and your whole game history? This cannot be undone.")) return;
    try {
      await api("/api/me", { method: "DELETE" });
      window.location.assign("/");
    } catch (error) { toast(error.message); }
  });

  load();
})();
