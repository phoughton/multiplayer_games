"""Tests for server/security.py — rate limiting, validation, origin checks."""

from __future__ import annotations

import time

from server.security import (
    ConnectionCounter,
    TokenBucket,
    is_origin_allowed,
    safe_compare,
    validate_message,
)


# ------------------------------------------------------------ safe_compare


def test_safe_compare_equal_strings_true():
    assert safe_compare("abc123", "abc123") is True


def test_safe_compare_different_strings_false():
    assert safe_compare("abc123", "abc124") is False


def test_safe_compare_handles_non_string():
    assert safe_compare("x", 123) is False  # type: ignore[arg-type]


# ------------------------------------------------------------ TokenBucket


def test_token_bucket_allows_up_to_capacity_immediately():
    b = TokenBucket(capacity=3, refill_per_sec=0.01)
    assert b.try_consume("ip") is True
    assert b.try_consume("ip") is True
    assert b.try_consume("ip") is True
    assert b.try_consume("ip") is False


def test_token_bucket_refills_over_time():
    b = TokenBucket(capacity=1, refill_per_sec=1000.0)
    assert b.try_consume("ip") is True
    assert b.try_consume("ip") is False
    time.sleep(0.01)
    assert b.try_consume("ip") is True


def test_token_bucket_keys_are_independent():
    b = TokenBucket(capacity=1, refill_per_sec=0.001)
    assert b.try_consume("a") is True
    assert b.try_consume("a") is False
    assert b.try_consume("b") is True


# ---------------------------------------------------------- ConnectionCounter


def test_connection_counter_enforces_limit():
    cc = ConnectionCounter(2)
    assert cc.acquire("ip") is True
    assert cc.acquire("ip") is True
    assert cc.acquire("ip") is False
    cc.release("ip")
    assert cc.acquire("ip") is True


def test_connection_counter_cleans_up_zero_counts():
    cc = ConnectionCounter(5)
    cc.acquire("ip")
    cc.release("ip")
    assert cc.count("ip") == 0


# ---------------------------------------------------------- origin check


def test_origin_wildcard_allows_all():
    assert is_origin_allowed("http://evil.example", ["*"]) is True
    assert is_origin_allowed(None, ["*"]) is True


def test_origin_explicit_allowlist_blocks_others():
    allowed = ["https://pong.example.com"]
    assert is_origin_allowed("https://pong.example.com", allowed) is True
    assert is_origin_allowed("https://evil.example.com", allowed) is False
    assert is_origin_allowed(None, allowed) is False


# ------------------------------------------------------- validate_message


def test_validate_rejects_oversize():
    raw = '{"type":"input","paddle_x":0.5}'
    assert validate_message(raw, max_bytes=10) is None


def test_validate_rejects_non_json():
    assert validate_message("not json", 1024) is None


def test_validate_rejects_non_object():
    assert validate_message('["array"]', 1024) is None


def test_validate_rejects_unknown_type():
    assert validate_message('{"type":"nuke"}', 1024) is None


def test_validate_input_range():
    ok = validate_message('{"type":"input","paddle_x":0.5}', 1024)
    assert ok == {"type": "input", "paddle_x": 0.5}
    assert validate_message('{"type":"input","paddle_x":-0.1}', 1024) is None
    assert validate_message('{"type":"input","paddle_x":1.5}', 1024) is None
    assert validate_message('{"type":"input","paddle_x":"a"}', 1024) is None


def test_validate_input_rejects_bool_masquerading_as_number():
    # In Python, bool is a subclass of int. Explicitly reject to prevent
    # accidentally accepting {"paddle_x": true} as 1.0.
    assert validate_message('{"type":"input","paddle_x":true}', 1024) is None


def test_validate_input_rejects_nan():
    import math
    raw = '{"type":"input","paddle_x":' + str(math.nan) + '}'
    assert validate_message(raw, 1024) is None


def test_validate_join_checks_code_shape():
    ok = validate_message('{"type":"join","code":"ABCDEF"}', 1024)
    assert ok == {"type": "join", "code": "ABCDEF"}
    # Wrong length
    assert validate_message('{"type":"join","code":"ABC"}', 1024) is None
    # Bad character (O is excluded from safe alphabet)
    assert validate_message('{"type":"join","code":"ABCDEO"}', 1024) is None


def test_validate_reconnect_requires_hex_token():
    ok = validate_message(
        '{"type":"reconnect","code":"ABCDEF","token":"abcd1234"}', 1024
    )
    assert ok is not None and ok["token"] == "abcd1234"
    bad = validate_message(
        '{"type":"reconnect","code":"ABCDEF","token":"not-hex"}', 1024
    )
    assert bad is None


def test_validate_bare_types_strip_extra_fields():
    ok = validate_message('{"type":"eject","extra":"ignored"}', 1024)
    assert ok == {"type": "eject"}
