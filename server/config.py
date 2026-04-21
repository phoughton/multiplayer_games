"""Environment-driven configuration for the Pong server.

All runtime tunables live here so production operators can adjust behaviour
via env vars without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(env_name: str, default: list[str]) -> list[str]:
    v = os.environ.get(env_name)
    if v is None:
        return list(default)
    return [x.strip() for x in v.split(",") if x.strip()]


def _int(env_name: str, default: int) -> int:
    try:
        return int(os.environ.get(env_name, default))
    except ValueError:
        return default


def _float(env_name: str, default: float) -> float:
    try:
        return float(os.environ.get(env_name, default))
    except ValueError:
        return default


def _bool(env_name: str, default: bool) -> bool:
    v = os.environ.get(env_name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # Network / trust
    allowed_origins: list[str]        # ws Origin allowlist; ["*"] disables check
    allowed_hosts: list[str]          # HTTP Host allowlist; ["*"] disables check

    # Resource limits
    max_rooms: int
    max_connections_per_ip: int
    ws_max_message_bytes: int

    # Timeouts
    handshake_timeout_s: float
    reconnect_grace_s: float
    stale_room_idle_s: float
    stale_sweep_interval_s: float

    # Rate limits (token-bucket)
    input_rate_per_sec: float
    input_burst: int
    join_rate_per_sec: float
    join_burst: int
    connect_rate_per_sec: float
    connect_burst: int

    # Misc
    log_level: str
    behind_proxy: bool               # if true, trust X-Forwarded-For for client IPs


def load_config() -> Config:
    return Config(
        allowed_origins=_csv("ALLOWED_ORIGINS", ["*"]),
        allowed_hosts=_csv("ALLOWED_HOSTS", ["*"]),
        max_rooms=_int("MAX_ROOMS", 500),
        max_connections_per_ip=_int("MAX_CONNECTIONS_PER_IP", 8),
        ws_max_message_bytes=_int("WS_MAX_MESSAGE_BYTES", 1024),
        handshake_timeout_s=_float("HANDSHAKE_TIMEOUT_S", 5.0),
        reconnect_grace_s=_float("RECONNECT_GRACE_S", 5.0),
        stale_room_idle_s=_float("STALE_ROOM_IDLE_S", 1800.0),
        stale_sweep_interval_s=_float("STALE_SWEEP_INTERVAL_S", 60.0),
        input_rate_per_sec=_float("INPUT_RATE_PER_SEC", 120.0),
        input_burst=_int("INPUT_BURST", 30),
        join_rate_per_sec=_float("JOIN_RATE_PER_SEC", 0.5),
        join_burst=_int("JOIN_BURST", 5),
        connect_rate_per_sec=_float("CONNECT_RATE_PER_SEC", 1.0),
        connect_burst=_int("CONNECT_BURST", 10),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        behind_proxy=_bool("BEHIND_PROXY", False),
    )


CONFIG = load_config()
