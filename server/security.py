"""Security helpers: rate limiting, connection counting, safe comparison,
Origin validation, and a JSON message validator.

These helpers are intentionally synchronous and dependency-free so they can
be unit-tested in isolation.
"""

from __future__ import annotations

import hmac
import json
import time
from collections import defaultdict
from typing import Any, Optional

from .rooms import CODE_ALPHABET, CODE_LENGTH

MAX_TOKEN_LEN = 128
ALLOWED_MESSAGE_TYPES = {
    "create", "join", "reconnect", "input", "eject", "rematch", "leave"
}


def safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison. Never short-circuit on prefix mismatch."""
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except (AttributeError, TypeError):
        return False


class TokenBucket:
    """Classic token-bucket limiter, keyed by an arbitrary string (typically IP).

    Buckets refill continuously at `refill_per_sec` up to `capacity`. Keys are
    garbage-collected when the dict grows beyond `max_keys` to prevent an
    attacker from exhausting memory by cycling source addresses.
    """

    def __init__(self, capacity: int, refill_per_sec: float, max_keys: int = 10_000) -> None:
        self.capacity = float(capacity)
        self.refill = float(refill_per_sec)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._max_keys = max_keys

    def try_consume(self, key: str, n: float = 1.0) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        if tokens >= n:
            tokens -= n
            self._buckets[key] = (tokens, now)
            return True
        self._buckets[key] = (tokens, now)
        if len(self._buckets) > self._max_keys:
            self._gc(now)
        return False

    def _gc(self, now: float) -> None:
        # Drop keys that have refilled completely (i.e. unused for a while).
        stale = [
            k for k, (tokens, last) in self._buckets.items()
            if tokens >= self.capacity and now - last > 60.0
        ]
        for k in stale:
            self._buckets.pop(k, None)
        # If still too big, drop arbitrary oldest half.
        if len(self._buckets) > self._max_keys:
            items = sorted(self._buckets.items(), key=lambda kv: kv[1][1])
            for k, _ in items[: len(items) // 2]:
                self._buckets.pop(k, None)


class ConnectionCounter:
    """Per-key concurrency limit (typically per-IP WebSocket connections)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[str, int] = defaultdict(int)

    def acquire(self, key: str) -> bool:
        if self._counts[key] >= self.limit:
            return False
        self._counts[key] += 1
        return True

    def release(self, key: str) -> None:
        cur = self._counts.get(key, 0)
        if cur <= 1:
            self._counts.pop(key, None)
        else:
            self._counts[key] = cur - 1

    def count(self, key: str) -> int:
        return self._counts.get(key, 0)


def is_origin_allowed(origin: Optional[str], allowed: list[str]) -> bool:
    """Validate a WebSocket Origin header against an allowlist.

    `allowed == ["*"]` disables the check (for LAN/dev). Missing origin is
    rejected when any explicit allowlist is configured.
    """
    if not allowed or "*" in allowed:
        return True
    if not origin:
        return False
    return origin in allowed


def validate_message(raw: str, max_bytes: int) -> Optional[dict]:
    """Parse and schema-check one incoming WS message. Returns dict or None.

    Never raises. Rejects unknown types, malformed payloads, oversize frames,
    invalid codes, or out-of-range floats.
    """
    if not isinstance(raw, str):
        return None
    if len(raw.encode("utf-8")) > max_bytes:
        return None
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None

    mtype = msg.get("type")
    if not isinstance(mtype, str) or mtype not in ALLOWED_MESSAGE_TYPES:
        return None

    if mtype == "input":
        px = msg.get("paddle_x")
        if isinstance(px, bool) or not isinstance(px, (int, float)):
            return None
        pxf = float(px)
        if pxf != pxf or pxf < 0.0 or pxf > 1.0:  # NaN / range
            return None
        return {"type": "input", "paddle_x": pxf}

    if mtype == "join":
        return _validated_room_ref(msg, include_token=False)

    if mtype == "reconnect":
        return _validated_room_ref(msg, include_token=True)

    # create, eject, rematch, leave — no payload expected
    return {"type": mtype}


def _validated_room_ref(msg: dict, include_token: bool) -> Optional[dict]:
    code = msg.get("code")
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    if len(code) != CODE_LENGTH or not all(c in CODE_ALPHABET for c in code):
        return None
    out: dict[str, Any] = {"type": msg["type"], "code": code}
    if include_token:
        token = msg.get("token")
        if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_LEN:
            return None
        if not all(c in "0123456789abcdefABCDEF" for c in token):
            return None
        out["token"] = token
    return out


def client_ip(scope_client: Optional[tuple], headers: dict, behind_proxy: bool) -> str:
    """Pick a caller identity. Prefer direct peer; fall back to XFF only if configured."""
    if behind_proxy:
        xff = headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    if scope_client:
        return scope_client[0] or "unknown"
    return "unknown"
