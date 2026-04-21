"""Tests for server/rooms.py matchmaking state machine."""

from __future__ import annotations

import random

import pytest

from server.rooms import CODE_ALPHABET, CODE_LENGTH, RoomError, RoomManager


def test_create_room_produces_valid_code_and_token():
    rm = RoomManager(rng=random.Random(0))
    code, token = rm.create_room()
    assert len(code) == CODE_LENGTH
    assert all(c in CODE_ALPHABET for c in code)
    assert len(token) > 16
    assert rm.authenticate(code, token) == 1


def test_join_room_fills_second_slot():
    rm = RoomManager(rng=random.Random(0))
    code, _ = rm.create_room()
    slot, token = rm.join_room(code)
    assert slot == 2
    assert rm.authenticate(code, token) == 2


def test_join_unknown_code_raises():
    rm = RoomManager(rng=random.Random(0))
    with pytest.raises(RoomError) as e:
        rm.join_room("ZZZZZZ")
    assert e.value.reason == "no_such_room"


def test_cannot_join_full_room():
    rm = RoomManager(rng=random.Random(0))
    code, _ = rm.create_room()
    rm.join_room(code)
    with pytest.raises(RoomError) as e:
        rm.join_room(code)
    assert e.value.reason == "full"


def test_eject_frees_the_slot():
    rm = RoomManager(rng=random.Random(0))
    code, _ = rm.create_room()
    rm.join_room(code)
    ejected = rm.eject(code, requester_slot=1)
    assert ejected == 2
    slot, _ = rm.join_room(code)
    assert slot == 2


def test_remove_player_deletes_empty_room():
    rm = RoomManager(rng=random.Random(0))
    code, _ = rm.create_room()
    deleted = rm.remove_player(code, 1)
    assert deleted is True
    assert not rm.room_exists(code)


def test_reconnect_via_matching_token():
    rm = RoomManager(rng=random.Random(0))
    code, token = rm.create_room()
    rm.set_connected(code, 1, False)
    assert rm.authenticate(code, token) == 1
    rm.set_connected(code, 1, True)


def test_reconnect_with_wrong_token_fails():
    rm = RoomManager(rng=random.Random(0))
    code, _ = rm.create_room()
    assert rm.authenticate(code, "not-the-token") is None


def test_rematch_requires_both_players():
    rm = RoomManager(rng=random.Random(0))
    code, _ = rm.create_room()
    rm.join_room(code)
    assert rm.request_rematch(code, 1) is False
    assert rm.request_rematch(code, 2) is True


def test_codes_are_unique_under_collision_pressure():
    # Pre-seed a manager with a lot of rooms and verify we still get unique codes.
    rm = RoomManager(rng=random.Random(42))
    seen = set()
    for _ in range(50):
        code, _ = rm.create_room()
        assert code not in seen
        seen.add(code)
