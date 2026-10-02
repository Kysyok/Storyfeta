# Storyfeta

A party game about collaborative story writing.
Everyone opens a story with one line. Stories are then passed around the circle, and each
writer sees only the last line of the story they receive. At the end the stories are
uncovered one line at a time, with authors shown, and everyone can react with 🔥 live.

Guests only need a nickname. Any player can optionally create an account (email + password) to
keep a nickname, default room settings and a history of every finished game they played.

## Run locally

```bash
docker compose up --build    # web + release (migrations) + worker + redis + postgres
```

Open <http://localhost:8000>, create a room, and join from other tabs/devices with the code or
invite link. The Docker build downloads the fonts (Caveat and LXGW WenKai Mono TC) once into
`app/static/fonts/`; they are served from your own app at runtime. Without them the site falls back
to system fonts. To fetch them without Docker: `python scripts/fetch_fonts.py`.

Without Docker: `uv sync`, copy `.env.example` to `.env`, start Redis (and Postgres, or leave
`DATABASE_URL` empty for guest-only mode), then `uv run alembic upgrade head` and
`uv run python -m app`.

```bash
uv run pytest        # game engine, timeouts, reactions, room service, snapshots, results, tokens, static URLs
uv run ruff check . && uv run ruff format .
```

## Processes (12-factor)

| Process | Command | Notes |
|---|---|---|
| web | `python -m app` | uvicorn, port from `PORT`, JSON logs to stdout, closes WebSockets on SIGTERM |
| release | `alembic upgrade head` | one-off admin command |
| worker | `python -m app.worker` | optional; deletes idle rooms |

All configuration is environment variables (`.env.example`). No state lives in process memory:
rooms, round deadlines and presence are in Redis, so a restarted process resumes running games.

## Structure

```
app/
  main.py, __main__.py, worker.py   app factory + lifespan, web entrypoint, cleanup worker
  core/        config (pydantic-settings), JSON logging, JWT + scrypt password hashing,
               assets.py (cache-busting static URLs)
  models/      domain.py (dataclasses in Redis), orm.py (SQLAlchemy, Postgres)
  game/        engine.py (all rules, pure), results.py, snapshot.py, archive.py, errors.py
  schemas/     http.py (REST bodies), ws.py (every WebSocket message)
  storage/     base.py (interfaces), redis_store.py, database.py
  services/    rooms.py (use-cases), timers.py, accounts.py, archive.py
  routers/     pages.py, rooms.py, accounts.py, deps.py
  ws/          endpoint.py (/ws/{code}), hub.py (sockets + Redis pub/sub)
  templates/   Jinja2        static/  css/, js/, fonts/, img/
alembic/       migrations       tests/   pytest, no Redis or sockets needed
scripts/       fetch_fonts.py (downloads the self-hosted fonts)
Dockerfile, docker-compose.yml, Procfile, .env.example
```

**Design:** follows the Figma mock-up, applied to every screen: translucent cards,
hand-drawn buttons, and the provided 1920x1080 background. The background is used as a nine-slice
border-image, so the wavy frame and corner doodles keep their shape on any landscape screen. Portrait
screens (phones) use the same picture turned upright; there the frame is part of the page, so it
scrolls with the content and stretches to the full page height. Swap assets without touching code, in
`app/static/img/`: `logo.svg` (header), `background.png` (keep it 1920x1080 with 300px corners),
`background-portrait.png` (1080x1920) and `deco-cross-purple.svg` (decorative cross on the home screen). Fonts: LXGW WenKai Mono TC for the
interface and Caveat for story text, both self-hosted (`python scripts/fetch_fonts.py`, run
automatically by the Docker build); a scaled system monospace is used until they are downloaded.
Static URLs are versioned by file modification time, so browsers never serve a stale script.

## How it works

* Players sit in a circle in join order. In round `r`, seat `p` writes on story `(p - r) mod n`.
* A player is shown only the last line actually written on that story.
* **Timeouts:** a deadline is stored in Redis. A loop in each web process ticks overdue rooms
  under a per-room Redis lock (idempotent, safe across processes and restarts). A player who
  did not write leaves no line; the next writer sees the last real line, or an empty story to open
  themselves.
* **Passing a line on:** "Pass it on" locks your line (the text can't be changed); the same button then reads
  "Edit line" and takes it back so you can edit and pass it on again. The next turn starts as soon as
  *every* player has a line passed on, or when the timer runs out. A line that was typed but not
  passed on in time is passed on as it is: the client keeps it on the server as a draft while typing.
* **Nicknames:** a signed-in player's profile nickname is used automatically when hosting or joining.
* **Coming back:** a player who leaves the room and joins again (same browser, or same signed-in
  account) gets their old seat back, even after the game has started.
* **Host leaves:** if the host has been offline for 10 seconds (a page reload doesn't count), the
  first online player in seat order becomes host.
* **History:** when a game finishes it is saved to the history of *every* signed-in player who took part
  (not only the host), shown in Profile. Guests' games are not saved.
* **Reveal:** the host uncovers line by line, story by story. Reactions toggle per player per emoji.
* **Results:** most-reacted lines (`game/results.py`). Archived to Postgres for each signed-in player.

## Data models

Active game (Redis, one JSON document per room, `app/models/domain.py`):

* **Room** `code, host_id, phase (lobby|writing|reveal|results), settings{rounds, turn_seconds}, players, stories, round_index, round_deadline, submitted_ids, drafts{player_id: text}, reveal{story_index, lines_shown}, archived, created_at, updated_at`
* **Player** `id, nickname, is_host, account_id`
* **Story** `index, starter_id, entries[]`
* **StoryEntry** `round_index, author_id, text, reactions[]`
* **Reaction** `emoji, player_ids[]`

Postgres (`app/models/orm.py`): **Account** `id, email, nickname, password_hash, default_rounds, default_turn_seconds, created_at`;
**ArchivedGame** `id, account_id, room_code, title, rounds, finished_at, payload (JSONB: players + stories with authors, text, reaction counts)`.

CRUD over the PostgreSQL entities (`services/accounts.py`, `routers/accounts.py`):

| Entity | Create | Read | Update | Delete |
|---|---|---|---|---|
| Account | `POST /api/auth/register` | `GET /api/me` | `PUT /api/me/profile`, `PUT /api/me/defaults` | `DELETE /api/me` |
| ArchivedGame | automatically when a game finishes | `GET /api/archive`, `GET /api/archive/{id}` | `PATCH /api/archive/{id}` | `DELETE /api/archive/{id}` |

## REST endpoints

| Method & path | Purpose |
|---|---|
| `POST /api/rooms` `{nickname?}` | create room as host; sets session cookie, returns `{code, player_id, url, token}` |
| `POST /api/rooms/{code}/join` `{nickname?}` | join the lobby; same response. `nickname` may be omitted when signed in (profile nickname is used). A player who already has a seat in the room (same browser cookie or account) gets that seat back instead, even mid-game |
| `GET /api/rooms/{code}` | public room info |
| `POST /api/auth/register` `{email, password, nickname?}`, `/login`, `/logout` | optional account |
| `GET /api/me`, `PUT /api/me/profile`, `PUT /api/me/defaults` | account, nickname, saved default settings |
| `DELETE /api/me` | delete the account and its history (cascade), sign out |
| `GET /api/archive`, `GET /api/archive/{id}` | finished games |
| `PATCH /api/archive/{id}` `{title}` | rename a saved game (`null`/empty resets to the room code) |
| `DELETE /api/archive/{id}` | remove a game from your history (other players keep theirs) |
| `GET /healthz` | health check: Redis and PostgreSQL (503 if either is down) |
| `GET /api/docs` | Swagger UI (OpenAPI schema at `/api/openapi.json`) |
| Pages: `/`, `/r/{code}`, `/account` (Profile), `/archive/{id}` | Jinja2 |

## WebSocket: `/ws/{code}`

Authenticated by the signed player cookie (or `?token=`). Close codes: 4401 no/invalid session,
4403 foreign `Origin` (cross-site WebSocket hijacking guard), 4404 room gone, 1001 server restarting
(client reconnects with backoff).

Client → server: `set_settings {rounds, turn_seconds|null}` (host, lobby) · `start_game` (host) ·
`submit_line {text}` · `save_draft {text}` (the unsent line, passed on if time runs out) · `unsubmit_line` (take your line back to edit it) · `reveal_next` (host) · `react {line_id, emoji}` · `ping`

Server → client: `state {state}` (full personalised snapshot after every change, including
`server_time` for the countdown) · `error {code, message}` · `pong`
