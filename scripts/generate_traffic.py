"""Play a few real games against a running Storyfeta, so the logs have something to show.

Each room: a host and 1-3 guests join over REST, connect to ``/ws/{code}``, write
every round, then the host reveals the stories while the guests react. Along the
way it makes some deliberate mistakes (unknown rooms, empty nickname, a guest
trying to start the game, a malformed WebSocket message) so 4xx lines and
rejected messages show up too.

    python scripts/generate_traffic.py --url http://$(minikube ip):30080 --rooms 5
"""

# ruff: noqa: S310, S311 - the URL is ours; randomness here is for variety, not security

from __future__ import annotations

import argparse
import asyncio
import json
import random
import urllib.error
import urllib.request
from urllib.parse import urlparse

import websockets

LINES = [
    "The kettle refused to boil until someone apologised.",
    "Nobody noticed the moon was slightly to the left.",
    "A pigeon delivered the wrong letter, on purpose.",
    "The map had one more street than the town.",
    "Grandma's cat filed a formal complaint.",
    "At midnight the library started returning books.",
    "The lift only went to floors that did not exist.",
    "Every umbrella in the city opened at once.",
]


def http(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """One JSON request; returns (status, body) even for 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, _json(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read())


def _json(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except ValueError:  # an HTML page
        return {}


async def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    return await asyncio.to_thread(http, base, method, path, body)


async def play(ws_base: str, code: str, token: str, *, host: bool, rounds: int) -> None:
    """Drive one player from lobby to results by reacting to state pushes."""
    async with websockets.connect(f"{ws_base}/ws/{code}?token={token}") as ws:
        await ws.send(json.dumps({"type": "ping"}))
        if not host:
            await ws.send(json.dumps({"type": "start_game"}))  # rejected: not the host
            await ws.send("{not json")  # rejected: bad_message
        written: set[int] = set()  # rounds this player already wrote (states arrive in bursts)
        revealed: set[tuple[int, int]] = set()
        reacted: set[str] = set()
        started = False
        async for raw in ws:
            message = json.loads(raw)
            if message["type"] != "state":
                continue
            state = message["state"]
            phase = state["phase"]
            if phase == "lobby" and host and not started and len(state["players"]) >= 2:
                started = True
                await ws.send(json.dumps({"type": "set_settings", "rounds": rounds}))
                await asyncio.sleep(0.3)  # let the others connect
                await ws.send(json.dumps({"type": "start_game"}))
            elif phase == "writing":
                writing = state["writing"]
                if writing["has_submitted"] or writing["round_index"] in written:
                    continue
                written.add(writing["round_index"])
                await asyncio.sleep(random.uniform(0.1, 0.6))  # "typing"
                await ws.send(json.dumps({"type": "submit_line", "text": random.choice(LINES)}))
            elif phase == "reveal":
                reveal = state["reveal"]
                if host:
                    key = (reveal["story_number"], len(reveal["lines"]))
                    if key not in revealed:
                        revealed.add(key)
                        await asyncio.sleep(0.5)  # give the guests time to react
                        await ws.send(json.dumps({"type": "reveal_next"}))
                else:
                    for line in reveal["lines"]:
                        if line["line_id"] not in reacted and random.random() < 0.6:
                            reacted.add(line["line_id"])
                            await ws.send(
                                json.dumps(
                                    {"type": "react", "line_id": line["line_id"], "emoji": "🔥"}
                                )
                            )
            elif phase == "results":
                return


async def room(base: str, ws_base: str, index: int, rounds: int) -> str:
    """One full game; returns the room code."""
    status, created = await call(base, "POST", "/api/rooms", {"nickname": f"host{index}"})
    if status != 201:
        raise RuntimeError(f"create room failed: {status} {created}")
    code = created["code"]
    players = [(created["token"], True)]
    for n in range(random.randint(1, 3)):
        status, joined = await call(
            base, "POST", f"/api/rooms/{code.lower()}/join", {"nickname": f"guest{index}-{n}"}
        )
        players.append((joined["token"], False))
    await call(base, "GET", f"/r/{code}")
    await call(base, "GET", f"/api/rooms/{code}")
    await asyncio.wait_for(
        asyncio.gather(
            *(play(ws_base, code, token, host=host, rounds=rounds) for token, host in players)
        ),
        timeout=120,
    )
    return code


async def mistakes(base: str) -> None:
    """Requests that should fail with 4xx."""
    await call(base, "GET", "/api/rooms/ZZZZ")
    await call(base, "POST", "/api/rooms/ZZZZ/join", {"nickname": "lost"})
    await call(base, "POST", "/api/rooms", {"nickname": "   "})
    await call(base, "GET", "/no-such-page")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="e.g. http://192.168.49.2:30080")
    parser.add_argument("--rooms", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    base = args.url.rstrip("/")
    parsed = urlparse(base)
    ws_base = f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.netloc}"

    await mistakes(base)
    results = await asyncio.gather(
        *(room(base, ws_base, i, args.rounds) for i in range(args.rooms)), return_exceptions=True
    )
    await mistakes(base)
    for result in results:
        print("room", result if isinstance(result, str) else f"failed: {result!r}")


if __name__ == "__main__":
    asyncio.run(main())
