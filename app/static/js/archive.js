/* A saved game, drawn from /api/archive/{id}; it can be renamed or deleted here. */
(async function () {
  const { api, h, showFormError, toast } = window.sf;
  const root = document.getElementById("archive-app");
  const url = `/api/archive/${root.dataset.gameId}`;
  const title = (game) => game.title || `Room ${game.room_code}`;
  try {
    const game = await api(url);
    document.getElementById("archive-title").textContent = title(game);
    document.getElementById("archive-sub").textContent =
      `${new Date(game.finished_at).toLocaleString()}, played by ${game.players.join(", ")}.`;
    document.getElementById("game-title").value = game.title || "";
    document.getElementById("game-title").placeholder = `Room ${game.room_code}`;
    document.getElementById("rename-form").hidden = false;
    document.getElementById("archive-stories").replaceChildren(...game.stories.map((story, i) => h(
      "section", { class: "archived-story" },
      h("h2", null, `Story ${i + 1}, started by ${story.starter}`),
      h("ol", { class: "strips" }, story.lines.map((line) => h(
        "li", { class: "strip" },
        h("blockquote", { class: "slip" }, line.text),
        h("div", { class: "strip__meta" },
          h("span", { class: "strip__author" }, line.author),
          line.reactions ? h("span", { class: "react" }, "🔥", h("span", { class: "react__count" }, line.reactions)) : null),
      ))),
    )));
  } catch (error) {
    root.replaceChildren(h("h1", null, "Not available"), h("p", { class: "lede" }, error.message),
      h("p", null, h("a", { class: "btn btn--secondary", href: "/" }, "Back to start")));
    return;
  }

  document.getElementById("rename-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    showFormError(event.target, "");
    try {
      const game = await api(url, { method: "PATCH", body: { title: document.getElementById("game-title").value } });
      document.getElementById("archive-title").textContent = title(game);
      document.getElementById("game-title").value = game.title || "";
      toast("Title saved.");
    } catch (error) { showFormError(event.target, error.message); }
  });

  document.getElementById("delete-game").addEventListener("click", async () => {
    if (!window.confirm("Delete this game from your history?")) return;
    try {
      await api(url, { method: "DELETE" });
      window.location.assign("/account");
    } catch (error) { toast(error.message); }
  });
})();
