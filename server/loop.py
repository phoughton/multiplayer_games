"""Async game tick loop for a single room."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .rooms import RoomManager

TICK_HZ = 30
TICK_SECONDS = 1.0 / TICK_HZ

Broadcaster = Callable[[dict], Awaitable[None]]


async def run_game_loop(
    code: str,
    room_manager: RoomManager,
    broadcast: Broadcaster,
    tick_seconds: float = TICK_SECONDS,
) -> None:
    """Drive a room's PongGame at TICK_HZ and broadcast state each tick.

    Exits cleanly once the room is gone.
    """
    loop = asyncio.get_running_loop()
    last = loop.time()
    try:
        while True:
            await asyncio.sleep(tick_seconds)
            now = loop.time()
            dt = now - last
            last = now
            room = room_manager.get_room(code)
            if room is None:
                return
            room.touch()

            both_present = len(room.players) == 2
            both_connected = both_present and all(
                p.connected for p in room.players.values()
            )

            if not both_connected:
                if room.game.state.status == "playing":
                    room.game.pause()
                # Reset dt baseline so we don't accumulate a huge elapsed
                # time across the pause.
                last = loop.time()
                await broadcast({"type": "state", "state": room.game.snapshot()})
                continue

            if room.game.state.status == "paused":
                room.game.resume_from_pause()
                last = loop.time()

            room.game.step(dt)
            await broadcast({"type": "state", "state": room.game.snapshot()})

            if room.game.state.status == "game_over" and room.game.state.winner:
                # Let clients act on the final frame then continue ticking
                # (game_over is a terminal status until rematch flips it back).
                pass
    except asyncio.CancelledError:
        raise
