/* Room screen: one WebSocket, one server-sent snapshot, four views.
   The server owns all state. This file only draws snapshots and sends intents. */
(function () {
  const { h, toast } = window.sf;
  const $ = (id) => document.getElementById(id);
  const app = $("app");
  const code = app.dataset.code;

  const views = { lobby: $("view-lobby"), writing: $("view-write"), reveal: $("view-reveal"), results: $("view-results") };
  const FATAL = { 4401: "Your seat in this room has expired.", 4403: "This connection was refused.", 4404: "This room has closed." };

  let state = null;       // latest snapshot
  let socket = null;
  let retry = 0;
  let pingTimer = null;
  let clockOffset = 0;    // server time minus local time, seconds
  let writeKey = null;    // round the writing view is showing
  let revealKey = null;   // story the reveal view is showing
  const stripEls = new Map();

  /* ---- connection ----------------------------------------------------------- */

  function connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/${code}`);
    socket.addEventListener("open", () => {
      retry = 0;
      $("connection").hidden = true;
      pingTimer = setInterval(() => send({ type: "ping" }, true), 25000);
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "state") render(message.state);
      else if (message.type === "error") { toast(message.message); $("submit-line").disabled = false; }
    });
    socket.addEventListener("close", (event) => {
      clearInterval(pingTimer);
      if (FATAL[event.code]) return fatal(FATAL[event.code]);
      $("connection").hidden = false;   // server restarting or network blip: retry with backoff
      setTimeout(connect, Math.min(1000 * 2 ** retry++, 8000));
    });
  }

  function send(message, quiet = false) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
    else if (!quiet) toast("Reconnecting… try again in a moment.");
  }

  function fatal(text) {
    app.replaceChildren(
      h("h1", null, "Out of the room"),
      h("p", { class: "lede" }, text),
      h("p", null, h("a", { class: "btn btn--berry", href: "/" }, "Back to start")),
    );
  }

  /* ---- render dispatch ------------------------------------------------------- */

  function render(snapshot) {
    state = snapshot;
    clockOffset = snapshot.server_time - Date.now() / 1000;
    $("loading").hidden = true;
    for (const [phase, el] of Object.entries(views)) el.hidden = phase !== snapshot.phase;
    ({ lobby: renderLobby, writing: renderWriting, reveal: renderReveal, results: renderResults })[snapshot.phase](snapshot);
  }

  /* ---- lobby ------------------------------------------------------------------- */

  const rounds = $("set-rounds"), seconds = $("set-seconds"), noTimer = $("set-notimer");
  let settingsTimer = null;

  function renderLobby(s) {
    const isHost = s.you.is_host;
    $("player-list").replaceChildren(...s.players.map((p) => h(
      "li", { class: "chip" + (p.is_host ? " chip--host" : "") + (p.id === s.you.id ? " you" : "") + (p.online ? "" : " offline") },
      p.nickname,
      p.is_host ? h("small", null, "host") : null,
      p.id === s.you.id ? h("small", null, "you") : null,
    )));

    rounds.min = s.limits.min_rounds; rounds.max = s.limits.max_rounds;
    seconds.min = s.limits.min_turn_seconds; seconds.max = s.limits.max_turn_seconds;
    setIfIdle(rounds, s.settings.rounds);
    setIfIdle(seconds, s.settings.turn_seconds ?? seconds.value ?? 60);
    noTimer.checked = s.settings.turn_seconds === null;
    seconds.disabled = !isHost || noTimer.checked;
    rounds.disabled = noTimer.disabled = !isHost;

    document.querySelectorAll("#settings-form .settings__row, #settings-form .settings__check")
      .forEach((el) => { el.hidden = !isHost; });
    const summary = $("settings-summary");
    summary.hidden = isHost;
    summary.textContent = `${s.settings.rounds} lines per story, ` +
      (s.settings.turn_seconds === null ? "no time limit." : `${s.settings.turn_seconds} seconds per turn.`);

    const enough = s.players.length >= s.limits.min_players;
    const start = $("start-game");
    start.hidden = !isHost;
    start.disabled = !enough;
    const wait = $("lobby-wait");
    wait.hidden = isHost && enough;
    wait.textContent = !isHost
      ? `Waiting for ${s.players.find((p) => p.is_host).nickname} to start the game.`
      : `You need at least ${s.limits.min_players} players to start.`;
  }

  function setIfIdle(input, value) {
    if (document.activeElement !== input) input.value = value;
  }

  function sendSettings() {
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(() => {
      const r = parseInt(rounds.value, 10);
      const t = noTimer.checked ? null : parseInt(seconds.value, 10);
      if (Number.isNaN(r) || (t !== null && Number.isNaN(t))) return;
      send({ type: "set_settings", rounds: r, turn_seconds: t });
    }, 350);
  }
  [rounds, seconds].forEach((el) => el.addEventListener("input", sendSettings));
  noTimer.addEventListener("change", sendSettings);
  $("settings-form").addEventListener("submit", (e) => e.preventDefault());
  $("start-game").addEventListener("click", () => send({ type: "start_game" }));

  /* ---- writing ----------------------------------------------------------------- */

  const input = $("line-input");

  let prevSubmitted = false;  // was the line locked in the previous snapshot?
  let sentRound = null;       // round in which this player passed a line on
  let savedDraft = "";        // the unsent line as the server last heard it
  let draftTimer = null;

  function renderWriting(s) {
    const w = s.writing;
    const key = String(w.round_index);
    $("write-title").textContent = `Line ${w.round_index + 1} of ${w.total_rounds}`;

    if (key !== writeKey) {
      // The round moved on before this player pressed the button: the server passed their draft on.
      if (writeKey !== null && sentRound !== writeKey && savedDraft) toast("Time's up: your line was passed on as it was.");
      writeKey = key;
      clearTimeout(draftTimer);
      input.value = w.my_draft || "";  // restored after a reload
      savedDraft = input.value.trim();
      input.maxLength = s.limits.max_line_length;

      const hasPrompt = w.prompt !== null;
      $("prompt").hidden = !hasPrompt;
      $("prompt-text").textContent = hasPrompt ? w.prompt : "";
      const empty = $("prompt-empty");
      empty.hidden = hasPrompt;
      empty.textContent = w.round_index === 0
        ? "Open a story with anything you like. The next writer will see only your line."
        : "Nobody has written on this story yet, so it's yours to open.";
    }

    // Passed on = locked: the text can't change until the player presses the button again.
    if (w.has_submitted && w.my_text !== null) input.value = w.my_text;
    input.readOnly = w.has_submitted;
    input.classList.toggle("is-locked", w.has_submitted);
    if (!w.has_submitted && (prevSubmitted || document.activeElement === document.body)) input.focus();
    prevSubmitted = w.has_submitted;
    updateCount();

    const button = $("submit-line");
    button.disabled = false;
    button.textContent = w.has_submitted ? "Edit line" : "Pass it on";
    $("submitted").hidden = !w.has_submitted;
    $("waiting-text").textContent = w.waiting_on.length
      ? `Waiting for ${w.waiting_on.join(", ")}.` : "Everyone is done.";

    $("timer").hidden = w.deadline === null;
    $("no-timer").hidden = w.deadline !== null;
    updateTimer();
  }

  function updateCount() {
    const max = state ? state.limits.max_line_length : 280;
    $("char-count").textContent = `${input.value.length} / ${max}`;
  }
  input.addEventListener("input", updateCount);

  /* The line being typed is kept on the server, so it still counts if time runs out
     before "Pass it on" is pressed. Sent shortly after typing pauses. */
  function saveDraft() {
    clearTimeout(draftTimer);
    if (!state || state.phase !== "writing" || input.readOnly) return;
    const text = input.value.trim();
    if (text === savedDraft) return;
    savedDraft = text;
    send({ type: "save_draft", text }, true);
  }
  input.addEventListener("input", () => {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraft, 300);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!input.readOnly) $("write-form").requestSubmit(); }
  });

  /** One button, two states: pass the line on (locks it) or take it back (unlocks it). */
  $("write-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.writing.has_submitted) {
      sentRound = null;
      savedDraft = input.value.trim();  // the server keeps the taken-back line as the draft
      send({ type: "unsubmit_line" });
    } else {
      if (!input.value.trim()) return toast("Write something before passing it on.");
      sentRound = writeKey;
      send({ type: "submit_line", text: input.value });
    }
    $("submit-line").disabled = true;
  });

  /** Countdown driven by the server's deadline, corrected for clock skew. */
  function updateTimer() {
    if (!state || state.phase !== "writing" || state.writing.deadline === null) return;
    const left = Math.max(0, state.writing.deadline - (Date.now() / 1000 + clockOffset));
    const whole = Math.ceil(left);
    $("timer-text").textContent = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
    $("timer-bar").style.width = `${Math.min(100, (left / state.settings.turn_seconds) * 100)}%`;
    $("timer").classList.toggle("urgent", left <= 10);
    if (left <= 1) saveDraft();  // last chance: don't wait for the typing pause
  }
  setInterval(updateTimer, 250);

  /* ---- reveal ------------------------------------------------------------------ */

  function renderReveal(s) {
    const r = s.reveal;
    const firstPaint = String(r.story_number) !== revealKey;
    if (firstPaint) {
      revealKey = String(r.story_number);
      stripEls.clear();
      $("strips").replaceChildren();
    }
    $("reveal-title").textContent = `Story ${r.story_number} of ${r.story_count}`;
    $("reveal-sub").textContent = `Started by ${r.starter}.`;
    $("reveal-empty").hidden = r.lines.length > 0;

    for (const line of r.lines) {
      let strip = stripEls.get(line.line_id);
      if (!strip) {
        strip = createStrip(line, s.reactions_allowed, !firstPaint);
        stripEls.set(line.line_id, strip);
        $("strips").append(strip);
        strip.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      updateReactions(strip, line, s.reactions_allowed);
    }

    const next = $("reveal-next");
    next.hidden = !s.you.is_host;
    $("reveal-wait").hidden = s.you.is_host;
    next.textContent = r.lines.length === 0 ? "Uncover first line"
      : r.lines.length < r.total_lines ? "Next line"
      : r.is_last_story ? "See the best lines" : "Next story";
  }

  function createStrip(line, allowed, animate) {
    const reactions = h("div", { class: "strip__reactions" });
    const strip = h(
      "li", { class: "strip" + (animate ? " strip--new" : ""), "data-line": line.line_id },
      h("blockquote", { class: "slip" }, line.text),
      h("div", { class: "strip__meta" }, h("span", { class: "strip__author" }, line.author), reactions),
    );
    for (const emoji of allowed) {
      reactions.append(h(
        "button", { class: "react", type: "button", "data-emoji": emoji, "aria-pressed": "false",
          "aria-label": `React with ${emoji}`, onclick: () => send({ type: "react", line_id: line.line_id, emoji }) },
        emoji, h("span", { class: "react__count" }),
      ));
    }
    return strip;
  }

  /** Update reaction buttons in place so focus and hover survive live updates. */
  function updateReactions(strip, line, allowed) {
    for (const emoji of allowed) {
      const button = strip.querySelector(`.react[data-emoji="${emoji}"]`);
      const info = line.reactions.find((x) => x.emoji === emoji);
      button.setAttribute("aria-pressed", info && info.mine ? "true" : "false");
      button.querySelector(".react__count").textContent = info && info.count ? info.count : "";
    }
  }
  $("reveal-next").addEventListener("click", () => send({ type: "reveal_next" }));

  /* ---- results ----------------------------------------------------------------- */

  function renderResults(s) {
    const emoji = s.reactions_allowed[0] || "🔥";
    const top = s.results.top_lines;
    $("no-top").hidden = top.length > 0;
    $("saved-note").hidden = !s.results.saved_to_archive;
    $("top-lines").replaceChildren(...top.map((line) => h(
      "li", { class: "strip" },
      h("blockquote", { class: "slip" }, line.text),
      h("div", { class: "strip__meta" },
        h("span", { class: "strip__author" }, `${line.author}, in ${line.story_starter}'s story`),
        h("span", { class: "react", "aria-label": `${line.reactions} reactions` }, emoji, h("span", { class: "react__count" }, line.reactions))),
    )));
  }

  /* ---- invite link ------------------------------------------------------------- */

  $("copy-link").addEventListener("click", async () => {
    const url = `${location.origin}/r/${code}`;
    try { await navigator.clipboard.writeText(url); } catch (_) { window.prompt("Copy this invite link:", url); return; }
    const hint = $("copy-hint");
    hint.textContent = "Copied";
    setTimeout(() => { hint.textContent = "Copy invite link"; }, 1500);
  });

  // If nothing arrives, say so instead of leaving "Opening the room..." on screen forever.
  setTimeout(() => {
    if (!state) $("loading").textContent = "Still connecting… Check your connection, or reload the page.";
  }, 8000);

  connect();
})();
