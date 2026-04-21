"""Periodic cleanup of idle rooms to prevent unbounded growth."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .rooms import RoomManager

logger = logging.getLogger("pong.sweeper")

DropCallback = Callable[[str], Awaitable[None]]


async def run_stale_room_sweeper(
    room_manager: RoomManager,
    drop_live_room: DropCallback,
    idle_seconds: float,
    interval_seconds: float,
) -> None:
    """Periodically drop rooms that have been idle longer than `idle_seconds`."""
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            codes = room_manager.stale_codes(idle_seconds)
            for code in codes:
                logger.info("dropping stale room: %s", code)
                # Purge all player slots. The room is deleted on last removal.
                room = room_manager.get_room(code)
                if room is None:
                    continue
                for slot in list(room.players.keys()):
                    room_manager.remove_player(code, slot)
                # Ensure async layer resources (runner task, sockets) are released.
                await drop_live_room(code)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # defensive: never die silently
        logger.exception("sweeper crashed: %s", exc)
        raise
