"""RoomService against in-memory fakes: locking, timers, notifications, archiving."""

from __future__ import annotations

import asyncio

import pytest

from app.game import engine
from app.game.archive import build_archive_payload
from app.game.errors import ForbiddenError, GameError, NotFoundError
from app.models.domain import Phase, Settings
from app.services.rooms import RoomService
from tests.fakes import MemoryStore, RecordingArchiver, RecordingPublisher


class Clock:
    """A clock the test can move."""

    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


def make(archiver=None):
    clock, store, pub = Clock(), MemoryStore(), RecordingPublisher()
    svc = RoomService(
        store, pub, engine.DEFAULT_RULES, Settings(rounds=2, turn_seconds=30),
        archiver=archiver, clock=clock,
    )
    return svc, store, pub, clock


async def lobby_of_three(svc, host_account=None, ann_account=None):
    room = await svc.create_room("h", "Host", account_id=host_account)
    await svc.join_room(room.code, "a", "Ann", ann_account)
    await svc.join_room(room.code, "b", "Bob")
    return room.code


def test_create_join_start_and_notifications():
    async def run():
        svc, store, pub, _ = make()
        code = await lobby_of_three(svc)
        assert len(code) == 4
        assert pub.changed == [code, code]  # one per join
        await svc.update_settings(code, "h", Settings(rounds=3, turn_seconds=None))
        await svc.start_game(code, "h")
        room = await svc.get_room(code.lower())  # codes are case-insensitive
        assert room.phase is Phase.WRITING and room.settings.turn_seconds is None
        assert code not in store.deadlines  # timer disabled -> nothing to fire
    asyncio.run(run())


def test_unknown_room_and_outsiders_are_rejected():
    async def run():
        svc, *_ = make()
        with pytest.raises(NotFoundError):
            await svc.get_room("ZZZZ")
        code = await lobby_of_three(svc)
        with pytest.raises(ForbiddenError) as err:
            await svc.start_game(code, "stranger")
        assert err.value.code == "not_in_room"
    asyncio.run(run())


def test_failed_action_changes_nothing_and_sends_no_event():
    async def run():
        svc, _, pub, _ = make()
        code = await lobby_of_three(svc)
        before = list(pub.changed)
        with pytest.raises(ForbiddenError):
            await svc.start_game(code, "a")  # not the host
        assert pub.changed == before
        assert (await svc.get_room(code)).phase is Phase.LOBBY
    asyncio.run(run())


def test_timer_loop_advances_overdue_room_and_skips_the_slow_player():
    async def run():
        svc, store, pub, clock = make()
        code = await lobby_of_three(svc)
        await svc.start_game(code, "h")
        assert store.deadlines[code] == clock.t + 30
        await svc.submit_line(code, "h", "Host opens")
        await svc.submit_line(code, "a", "Ann opens")
        # Bob is away. Before the deadline the timer loop does nothing...
        clock.t += 29
        assert await svc.tick_due() == 0
        # ...after it, the game moves on without him.
        clock.t += 2
        assert await svc.tick_due() == 1
        room = await svc.get_room(code)
        assert room.round_index == 1 and room.submitted_ids == []
        assert await svc.tick_due() == 0  # idempotent
    asyncio.run(run())


def test_concurrent_ticks_from_two_processes_advance_only_once():
    async def run():
        svc, _, _, clock = make()
        code = await lobby_of_three(svc)
        await svc.start_game(code, "h")
        clock.t += 31
        results = await asyncio.gather(svc.tick(code), svc.tick(code), svc.tick(code))
        assert sorted(results) == [False, False, True]
        assert (await svc.get_room(code)).round_index == 1
    asyncio.run(run())


def test_state_survives_a_process_restart():
    async def run():
        svc, store, pub, clock = make()
        code = await lobby_of_three(svc)
        await svc.start_game(code, "h")
        await svc.submit_line(code, "h", "before restart")
        # A brand-new service over the same store == a restarted process.
        svc2 = RoomService(store, pub, engine.DEFAULT_RULES, Settings(), clock=clock)
        clock.t += 31
        assert await svc2.tick_due() == 1
        assert (await svc2.get_room(code)).stories[0].entries[0].text == "before restart"
    asyncio.run(run())


async def play_to_results(svc, clock, code):
    await svc.start_game(code, "h")
    for round_no in range(2):
        for pid in ("h", "a", "b"):
            await svc.submit_line(code, pid, f"{pid} line {round_no}")
    room = await svc.get_room(code)
    assert room.phase is Phase.REVEAL
    while room.phase is Phase.REVEAL:
        await svc.reveal_next(code, "h")
        room = await svc.get_room(code)
        if room.phase is Phase.REVEAL and room.reveal.lines_shown:
            line = engine.line_id(room.reveal.story_index, room.reveal.lines_shown - 1)
            await svc.react(code, "a", line, "🔥")
            room = await svc.get_room(code)
    return room


def test_game_is_archived_once_for_the_room_and_recorded_for_each_signed_in_player():
    async def run():
        archiver = RecordingArchiver()
        svc, _, _, clock = make(archiver)
        # The host is a guest; Ann and Bob-less: only Ann is signed in.
        code = await lobby_of_three(svc, host_account=None, ann_account="acct-ann")
        room = await play_to_results(svc, clock, code)
        assert room.phase is Phase.RESULTS and room.archived
        assert len(archiver.archived) == 1  # one archive call; the archiver fans out per account
        payload = build_archive_payload(archiver.archived[0])
        assert payload["players"] == ["Host", "Ann", "Bob"]
        assert len(payload["stories"]) == 3
        assert payload["stories"][0]["lines"][0]["reactions"] == 1
        accounts = {p.account_id for p in archiver.archived[0].players if p.account_id}
        assert accounts == {"acct-ann"}
    asyncio.run(run())


def test_guest_rooms_are_never_archived():
    async def run():
        archiver = RecordingArchiver()
        svc, _, _, clock = make(archiver)
        code = await lobby_of_three(svc)  # no owner
        room = await play_to_results(svc, clock, code)
        assert room.phase is Phase.RESULTS and not room.archived
        assert archiver.archived == []
    asyncio.run(run())


def test_archive_failure_does_not_break_the_game():
    async def run():
        svc, *_, clock = make(RecordingArchiver(fail=True))
        code = await lobby_of_three(svc, host_account="acct-1")
        room = await play_to_results(svc, clock, code)
        assert room.phase is Phase.RESULTS and not room.archived
    asyncio.run(run())


def test_delete_idle_rooms():
    async def run():
        svc, store, _, clock = make()
        old = await lobby_of_three(svc)
        clock.t += 7_200
        fresh = await svc.create_room("x", "Xena")
        assert await svc.delete_idle(3_600) == 1
        assert await store.load(old) is None and await store.load(fresh.code)
    asyncio.run(run())


def test_engine_errors_carry_codes():
    async def run():
        svc, *_ = make()
        code = await lobby_of_three(svc)
        with pytest.raises(GameError) as err:
            await svc.join_room(code, "z", "ann")
        assert err.value.code == "nickname_taken"
    asyncio.run(run())


def test_host_is_handed_to_the_first_online_player_when_host_leaves():
    async def run():
        svc, _, pub, _ = make()
        code = await lobby_of_three(svc)
        assert not await svc.hand_off_host(code, {"h", "a", "b"})  # host still here
        assert not await svc.hand_off_host(code, set())  # nobody to take over
        pub.changed.clear()
        assert await svc.hand_off_host(code, {"b", "a"})
        room = await svc.get_room(code)
        assert room.host_id == "a"  # seat order, not set order
        assert [p.id for p in room.players if p.is_host] == ["a"]
        assert pub.changed == [code]
        await svc.start_game(code, "a")  # the new host can act
        with pytest.raises(ForbiddenError):
            await svc.reveal_next(code, "h")
    asyncio.run(run())


def test_drafts_are_saved_quietly_and_survive_a_reload():
    async def run():
        svc, _, pub, clock = make()
        code = await lobby_of_three(svc)
        await svc.start_game(code, "h")
        pub.changed.clear()
        await svc.save_draft(code, "a", "typing...")
        assert pub.changed == []  # nobody else's screen changes
        room = await svc.get_room(code)
        assert room.drafts == {"a": "typing..."}
        clock.t += 30
        assert await svc.tick(code)
        room = await svc.get_room(code)
        assert [e.text for s in room.stories for e in s.entries] == ["typing..."]
    asyncio.run(run())
