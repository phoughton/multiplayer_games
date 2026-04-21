#!/usr/bin/env bash
# Development launcher. For production see README.md (TLS, env vars).
set -euo pipefail
cd "$(dirname "$0")"

# Tighten the websocket frame size to match application limits. Anything
# larger is rejected before it reaches our validator.
: "${WS_MAX_SIZE:=4096}"

exec uvicorn server.main:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-8000}" \
    --ws-max-size "${WS_MAX_SIZE}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
    "$@"
