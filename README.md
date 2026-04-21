# Multiplayer Games Server

A Python server that hosts multiplayer browser games. The reference game is **Pong** — a retro, black-and-white, two-player match played on phones.

## Local / LAN quick start

```bash
pip install -r requirements.txt
./run.sh
```

The server binds to `0.0.0.0:8000`. Find your LAN IP:

- Linux: `hostname -I`
- macOS: `ipconfig getifaddr en0`
- Windows: `ipconfig`

On each phone (on the same Wi-Fi), open `http://<your-lan-ip>:8000`.

## How to play Pong

1. Player A taps **START NEW GAME** and reads the 6-character code on screen.
2. Player B taps **JOIN WITH CODE** and types it in.
3. Your paddle is always at the bottom of **your** screen. Drag your finger left/right to move it.
4. First to 5 points wins — a point goes to the player who did **not** let the ball through.
5. On **GAME OVER**, **PLAY AGAIN** (both players must tap) or **QUIT**.
6. Either player can **EJECT** mid-match. If connection drops, the client auto-retries for 5 s.

## Production deployment

The server is hardened for public deployment but there are things it does **not** do itself — you must:

### 1. Terminate TLS at a reverse proxy

Put nginx / Caddy / Cloudflare in front. The app does **not** listen on 443 and does **not** handle certificates. Example Caddy config:

```caddyfile
pong.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

When running behind a proxy, set:

```bash
BEHIND_PROXY=true
FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8
```

so that per-IP rate limits see the real client, not the proxy.

### 2. Lock down Origin and Host allowlists

The defaults (`*`) are LAN-friendly but insecure. Set explicit values:

```bash
export ALLOWED_ORIGINS="https://pong.example.com"
export ALLOWED_HOSTS="pong.example.com"
```

This blocks Cross-Site WebSocket Hijacking and Host-header attacks.

### 3. Configure resource limits (defaults are sensible but review)

| Var | Default | What it does |
| --- | --- | --- |
| `MAX_ROOMS` | `500` | Cap concurrent rooms; new `create` requests beyond this get `server_full`. |
| `MAX_CONNECTIONS_PER_IP` | `8` | Max concurrent WebSockets from one IP. |
| `WS_MAX_MESSAGE_BYTES` | `1024` | Per-frame size cap, enforced in the application. |
| `WS_MAX_SIZE` | `4096` | Transport-level frame size passed to uvicorn. |
| `HANDSHAKE_TIMEOUT_S` | `5` | Close sockets that don't send a valid handshake in time. |
| `RECONNECT_GRACE_S` | `5` | How long a slot waits for a reconnect after disconnect. |
| `STALE_ROOM_IDLE_S` | `1800` | Rooms idle longer than this are swept (30 min). |
| `STALE_SWEEP_INTERVAL_S` | `60` | How often the sweeper runs. |
| `INPUT_RATE_PER_SEC` | `120` | Sustained paddle-input rate per IP. |
| `INPUT_BURST` | `30` | Short burst the limiter will tolerate. |
| `JOIN_RATE_PER_SEC` | `0.5` | Brute-force protection on code-joining (≈1 per 2 s). |
| `JOIN_BURST` | `5` | Initial join attempts allowed before throttling. |
| `CONNECT_RATE_PER_SEC` | `1` | New WebSocket upgrades per IP per second. |
| `CONNECT_BURST` | `10` | Initial connection burst. |
| `LOG_LEVEL` | `INFO` | Standard logging level. |
| `BEHIND_PROXY` | `false` | Trust `X-Forwarded-For` for rate-limiting keys. |

### 4. Run with dev dependencies separated

Production image installs only `requirements.txt`:

```bash
pip install --no-cache-dir -r requirements.txt
```

Development / CI use `requirements-dev.txt`.

## Security posture

What's enforced in-app:

- **Origin allowlist on `/ws`** — rejects the upgrade before `accept()` (CSWSH protection).
- **TrustedHost middleware** when `ALLOWED_HOSTS` is not `*` (Host-header attack protection).
- **Security response headers** on every HTTP response: `Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`.
- **Per-IP concurrency cap** and **per-IP token-bucket rate limits** on connect / join / input message rates.
- **Strict per-message schema validation** — oversized frames, malformed JSON, unknown message types, out-of-range numbers, NaN, wrong-length codes, non-hex tokens all rejected and logged.
- **Handshake timeout** — sockets that connect but send nothing are closed after 5 s.
- **Constant-time session-token comparison** (HMAC `compare_digest`).
- **Unpredictable room codes** — generated with `secrets.SystemRandom` from a 32-char ambiguity-free alphabet (~1.07B combinations).
- **Stale-room sweeper** — idle rooms are dropped, preventing slow leaks.
- **Tight uvicorn WS limits** via `--ws-max-size` and proxy-header trust.

What's **not** in-app — operator responsibility:

- TLS (use a reverse proxy).
- Denial-of-service protection at the edge (Cloudflare / WAF).
- Persistent user identity / accounts — this is a pairing-code game, not an auth system.
- Audit logging / SIEM integration — we log to stdout.

## Repository layout

```
server/
  config.py      Env-driven tunables
  security.py    Rate limiting, validation, origin check, safe compare
  rooms.py       RoomManager: create, join, eject, reconnect (constant-time)
  game.py        Pure PongGame physics & scoring
  loop.py        30 Hz async tick per room
  sweeper.py     Idle-room garbage collector
  main.py        FastAPI app wiring, middleware, WS endpoint
static/          Single-page client (HTML/JS/CSS)
tests/           pytest suite (game, rooms, security)
```

## Run tests

```bash
pip install -r requirements-dev.txt
pytest
```
