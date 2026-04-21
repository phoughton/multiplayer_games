"""FastAPI entrypoint for the multiplayer Pong server.

Hardened for production:
- Origin & Host allowlists (CSWSH / Host-header protection)
- Per-IP connection cap and token-bucket rate limits (connect / join / input)
- Handshake timeout; each WS must present a valid handshake within N seconds
- Strict per-message schema + size validation (no bare JSON parsing)
- Constant-time token comparison for reconnects
- Security response headers on all HTTP routes
- Room count cap and periodic stale-room sweeper
- Structured error responses that never leak internal state
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import CONFIG
from .loop import run_game_loop
from .rooms import RoomError, RoomManager
from .security import (
    ConnectionCounter,
    TokenBucket,
    client_ip,
    is_origin_allowed,
    validate_message,
)
from .sweeper import run_stale_room_sweeper

logging.basicConfig(
    level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("pong.server")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------------------------------------------------------------- middleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), accelerometer=(), gyroscope=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response


# -------------------------------------------------------------- live session


class LiveRoom:
    def __init__(self) -> None:
        self.sockets: dict[int, WebSocket] = {}
        self.grace_tasks: dict[int, asyncio.Task] = {}
        self.runner_task: Optional[asyncio.Task] = None


class Session:
    def __init__(self) -> None:
        self.rooms = RoomManager(max_rooms=CONFIG.max_rooms)
        self._live: dict[str, LiveRoom] = {}
        self.connections = ConnectionCounter(CONFIG.max_connections_per_ip)
        self.connect_bucket = TokenBucket(
            CONFIG.connect_burst, CONFIG.connect_rate_per_sec
        )
        self.join_bucket = TokenBucket(CONFIG.join_burst, CONFIG.join_rate_per_sec)
        self.input_bucket = TokenBucket(
            CONFIG.input_burst, CONFIG.input_rate_per_sec
        )

    def live_room(self, code: str) -> LiveRoom:
        return self._live.setdefault(code, LiveRoom())

    async def drop_live_room(self, code: str) -> None:
        live = self._live.pop(code, None)
        if not live:
            return
        if live.runner_task:
            live.runner_task.cancel()
        for task in live.grace_tasks.values():
            task.cancel()
        for ws in list(live.sockets.values()):
            try:
                await ws.close(code=1000)
            except Exception:
                pass

    async def broadcast(
        self, code: str, message: dict, exclude_slot: Optional[int] = None
    ) -> None:
        live = self._live.get(code)
        if not live:
            return
        dead: list[int] = []
        for slot, ws in list(live.sockets.items()):
            if slot == exclude_slot:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(slot)
        for slot in dead:
            live.sockets.pop(slot, None)

    async def send(self, ws: WebSocket, message: dict) -> None:
        try:
            await ws.send_json(message)
        except Exception:
            pass

    def ensure_runner(self, code: str) -> None:
        room = self.rooms.get_room(code)
        if room is None or len(room.players) < 2:
            return
        live = self.live_room(code)
        if live.runner_task is None or live.runner_task.done():
            room.game.start_countdown()
            live.runner_task = asyncio.create_task(
                run_game_loop(code, self.rooms, lambda m: self.broadcast(code, m))
            )
            live.runner_task.add_done_callback(_log_runner_exit)


def _log_runner_exit(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("game loop task failed: %r", exc, exc_info=exc)


# ---------------------------------------------------------------- lifecycle


session = Session()
_sweeper_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _sweeper_task
    _sweeper_task = asyncio.create_task(
        run_stale_room_sweeper(
            session.rooms,
            session.drop_live_room,
            CONFIG.stale_room_idle_s,
            CONFIG.stale_sweep_interval_s,
        )
    )
    try:
        yield
    finally:
        if _sweeper_task:
            _sweeper_task.cancel()
            try:
                await _sweeper_task
            except (asyncio.CancelledError, Exception):
                pass
        for code in list(session._live.keys()):
            await session.drop_live_room(code)


app = FastAPI(title="Pong multiplayer server", lifespan=lifespan)

if CONFIG.allowed_hosts and "*" not in CONFIG.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=CONFIG.allowed_hosts)
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------- websocket


async def _reject(ws: WebSocket, reason: str, close_code: int = 1008) -> None:
    try:
        await ws.send_json({"type": "error", "reason": reason})
    except Exception:
        pass
    try:
        await ws.close(code=close_code)
    except Exception:
        pass


async def _receive_valid(ws: WebSocket) -> Optional[dict]:
    """Receive one text frame, size-check it, parse & validate. Returns dict or None."""
    try:
        raw = await ws.receive_text()
    except Exception:
        return None
    return validate_message(raw, CONFIG.ws_max_message_bytes)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    headers = {k.decode().lower(): v.decode() for k, v in ws.scope.get("headers", [])}
    scope_client = ws.scope.get("client")
    ip = client_ip(scope_client, headers, CONFIG.behind_proxy)
    origin = headers.get("origin")

    # Origin allowlist (CSWSH protection). Reject *before* accepting the upgrade.
    if not is_origin_allowed(origin, CONFIG.allowed_origins):
        logger.warning("ws rejected bad origin ip=%s origin=%r", ip, origin)
        await ws.close(code=1008)
        return

    if not session.connect_bucket.try_consume(ip):
        logger.warning("ws rejected connect rate ip=%s", ip)
        await ws.close(code=1013)
        return

    if not session.connections.acquire(ip):
        logger.warning("ws rejected too-many-connections ip=%s", ip)
        await ws.close(code=1013)
        return

    await ws.accept()
    code: Optional[str] = None
    slot: Optional[int] = None

    try:
        # Phase 1: handshake — bounded wait so orphaned sockets are shed.
        try:
            async with asyncio.timeout(CONFIG.handshake_timeout_s):
                while code is None:
                    msg = await _receive_valid(ws)
                    if msg is None:
                        await _reject(ws, "bad_message")
                        return
                    result = await _handle_handshake(ws, msg, ip)
                    if result is None:
                        # Error already reported; keep waiting for valid handshake
                        # within the overall timeout window.
                        continue
                    code, slot = result
        except asyncio.TimeoutError:
            logger.info("ws handshake timeout ip=%s", ip)
            await _reject(ws, "handshake_timeout")
            return

        assert code is not None and slot is not None
        if session.rooms.get_room(code) is None:
            return

        # Phase 2: main loop.
        while True:
            msg = await _receive_valid(ws)
            if msg is None:
                await _reject(ws, "bad_message")
                return
            await _handle_in_room(ws, msg, code, slot, ip)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("websocket error ip=%s: %s", ip, exc)
    finally:
        session.connections.release(ip)
        if code is not None and slot is not None:
            await _handle_disconnect(code, slot)


async def _handle_handshake(
    ws: WebSocket, msg: dict, ip: str
) -> Optional[tuple[str, int]]:
    mtype = msg["type"]

    if mtype == "create":
        try:
            code, token = session.rooms.create_room()
        except RoomError as e:
            await session.send(ws, {"type": "error", "reason": e.reason})
            return None
        slot = 1
        session.live_room(code).sockets[slot] = ws
        await session.send(
            ws, {"type": "room_created", "code": code, "slot": slot, "token": token}
        )
        session.ensure_runner(code)
        return code, slot

    if mtype == "join":
        if not session.join_bucket.try_consume(ip):
            await session.send(ws, {"type": "error", "reason": "rate_limited"})
            return None
        try:
            slot, token = session.rooms.join_room(msg["code"])
        except RoomError as e:
            await session.send(ws, {"type": "error", "reason": e.reason})
            return None
        code = msg["code"]
        session.live_room(code).sockets[slot] = ws
        await session.send(
            ws, {"type": "joined", "code": code, "slot": slot, "token": token}
        )
        await session.broadcast(
            code, {"type": "opponent_joined"}, exclude_slot=slot
        )
        session.ensure_runner(code)
        return code, slot

    if mtype == "reconnect":
        r_code = msg["code"]
        r_token = msg["token"]
        r_slot = session.rooms.authenticate(r_code, r_token)
        if r_slot is None:
            await session.send(ws, {"type": "error", "reason": "bad_reconnect"})
            return None
        live = session.live_room(r_code)
        task = live.grace_tasks.pop(r_slot, None)
        if task:
            task.cancel()
        live.sockets[r_slot] = ws
        session.rooms.set_connected(r_code, r_slot, True)
        await session.send(
            ws, {"type": "reconnected", "code": r_code, "slot": r_slot}
        )
        session.ensure_runner(r_code)
        return r_code, r_slot

    # input/eject/rematch/leave are only valid after a handshake.
    await session.send(ws, {"type": "error", "reason": "bad_handshake"})
    return None


async def _handle_in_room(
    ws: WebSocket, msg: dict, code: str, slot: int, ip: str
) -> None:
    mtype = msg["type"]
    if mtype == "input":
        if not session.input_bucket.try_consume(ip):
            return
        r = session.rooms.get_room(code)
        if r is None:
            return
        view_x = msg["paddle_x"]
        canonical_x = 1.0 - view_x if slot == 2 else view_x
        r.game.apply_input(slot, canonical_x)
        r.touch()
    elif mtype == "eject":
        ejected = session.rooms.eject(code, slot)
        if ejected is not None:
            live = session.live_room(code)
            target_ws = live.sockets.pop(ejected, None)
            if target_ws is not None:
                await session.send(target_ws, {"type": "ejected"})
                try:
                    await target_ws.close(code=1000)
                except Exception:
                    pass
            grace = live.grace_tasks.pop(ejected, None)
            if grace:
                grace.cancel()
            await session.broadcast(
                code, {"type": "opponent_left", "reason": "ejected"}
            )
    elif mtype == "rematch":
        started = session.rooms.request_rematch(code, slot)
        if started:
            await session.broadcast(code, {"type": "rematch_started"})
        else:
            await session.broadcast(
                code, {"type": "rematch_requested", "slot": slot}
            )
    elif mtype == "leave":
        raise WebSocketDisconnect()
    else:
        # Handshake-only types (create/join/reconnect) received mid-session.
        await session.send(ws, {"type": "error", "reason": "bad_message"})


async def _handle_disconnect(code: str, slot: int) -> None:
    live = session.live_room(code)
    live.sockets.pop(slot, None)
    session.rooms.set_connected(code, slot, False)
    await session.broadcast(code, {"type": "opponent_disconnected", "slot": slot})

    async def grace() -> None:
        try:
            await asyncio.sleep(CONFIG.reconnect_grace_s)
        except asyncio.CancelledError:
            return
        deleted = session.rooms.remove_player(code, slot)
        await session.broadcast(
            code, {"type": "opponent_left", "reason": "disconnect"}
        )
        if deleted:
            await session.drop_live_room(code)

    existing = live.grace_tasks.pop(slot, None)
    if existing:
        existing.cancel()
    live.grace_tasks[slot] = asyncio.create_task(grace())
