"""Room / matchmaking model for the multiplayer game server.

Synchronous and side-effect-free beyond mutating its own dict. The async
websocket layer wraps this with broadcasts and disconnect grace timers.
"""

from __future__ import annotations

import hmac
import random
import secrets
import time
from dataclasses import dataclass, field

from .game import PongGame

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
DEFAULT_MAX_ROOMS = 500


@dataclass
class Player:
    slot: int
    token: str
    connected: bool = True
    disconnected_at: float | None = None


@dataclass
class Room:
    code: str
    game: PongGame
    players: dict[int, Player] = field(default_factory=dict)
    rematch_requests: set[int] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_activity_at = time.time()


class RoomError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RoomManager:
    def __init__(
        self,
        rng: random.Random | None = None,
        max_rooms: int = DEFAULT_MAX_ROOMS,
    ) -> None:
        self._rooms: dict[str, Room] = {}
        # Non-deterministic by default so codes can't be predicted from seed.
        self._rng = rng or random.SystemRandom()
        self.max_rooms = max_rooms

    def _new_code(self) -> str:
        for _ in range(1000):
            code = "".join(self._rng.choices(CODE_ALPHABET, k=CODE_LENGTH))
            if code not in self._rooms:
                return code
        raise RoomError("code_exhausted")

    def create_room(self) -> tuple[str, str]:
        if len(self._rooms) >= self.max_rooms:
            raise RoomError("server_full")
        code = self._new_code()
        token = secrets.token_hex(16)
        room = Room(code=code, game=PongGame(rng=random.Random()))
        room.players[1] = Player(slot=1, token=token)
        self._rooms[code] = room
        return code, token

    def join_room(self, code: str) -> tuple[int, str]:
        code = code.upper()
        room = self._rooms.get(code)
        if room is None:
            raise RoomError("no_such_room")
        if len(room.players) >= 2:
            raise RoomError("full")
        slot = 2 if 1 in room.players else 1
        token = secrets.token_hex(16)
        room.players[slot] = Player(slot=slot, token=token)
        # Fresh game when the second player arrives.
        if len(room.players) == 2:
            room.game = PongGame(rng=random.Random())
        return slot, token

    def authenticate(self, code: str, token: str) -> int | None:
        room = self._rooms.get(code)
        if room is None:
            return None
        if not isinstance(token, str):
            return None
        token_b = token.encode("utf-8")
        for p in room.players.values():
            if hmac.compare_digest(p.token.encode("utf-8"), token_b):
                return p.slot
        return None

    def get_room(self, code: str) -> Room | None:
        return self._rooms.get(code)

    def set_connected(self, code: str, slot: int, connected: bool) -> None:
        room = self._rooms.get(code)
        if not room or slot not in room.players:
            return
        p = room.players[slot]
        p.connected = connected
        p.disconnected_at = None if connected else time.time()

    def remove_player(self, code: str, slot: int) -> bool:
        """Remove a player slot. Returns True if the room was deleted."""
        room = self._rooms.get(code)
        if room is None:
            return False
        room.players.pop(slot, None)
        room.rematch_requests.discard(slot)
        if not room.players:
            del self._rooms[code]
            return True
        # With only one player left we reset the game state so the next
        # joiner gets a fresh match.
        room.game = PongGame(rng=random.Random())
        room.rematch_requests.clear()
        return False

    def eject(self, code: str, requester_slot: int) -> int | None:
        """Returns the slot that was ejected, or None if nothing to eject."""
        room = self._rooms.get(code)
        if room is None or requester_slot not in room.players:
            return None
        other = 2 if requester_slot == 1 else 1
        if other not in room.players:
            return None
        room.players.pop(other)
        room.rematch_requests.discard(other)
        room.game = PongGame(rng=random.Random())
        return other

    def request_rematch(self, code: str, slot: int) -> bool:
        """Record a rematch request. Returns True once both players have agreed."""
        room = self._rooms.get(code)
        if room is None or slot not in room.players:
            return False
        room.rematch_requests.add(slot)
        if len(room.players) == 2 and room.rematch_requests == set(room.players.keys()):
            room.rematch_requests.clear()
            room.game.rematch()
            return True
        return False

    def room_exists(self, code: str) -> bool:
        return code in self._rooms

    def active_codes(self) -> list[str]:
        return list(self._rooms.keys())

    def stale_codes(self, idle_seconds: float, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        return [
            code for code, room in self._rooms.items()
            if now - room.last_activity_at > idle_seconds
        ]

    def touch(self, code: str) -> None:
        room = self._rooms.get(code)
        if room is not None:
            room.touch()
