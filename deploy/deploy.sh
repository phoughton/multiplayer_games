#!/usr/bin/env bash
# Local driver for deploying the Pong server to a Hetzner box.
#
# Usage:
#   export PETERHOUGHTONCOM_PAT=<github-pat>
#   ./deploy/deploy.sh
#
# Overridable env vars (defaults shown):
#   DEPLOY_HOST=178.104.213.233
#   DEPLOY_USER=root
#   DEPLOY_DOMAIN=www.peterhoughton.com
#   DEPLOY_APEX=peterhoughton.com
#   LETSENCRYPT_EMAIL=pete@investigatingsoftware.co.uk
#   REPO_URL=https://github.com/phoughton/multiplayer_games.git
#   APP_BRANCH=main

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-178.104.213.233}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_DOMAIN="${DEPLOY_DOMAIN:-www.peterhoughton.com}"
DEPLOY_APEX="${DEPLOY_APEX:-peterhoughton.com}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-pete@investigatingsoftware.co.uk}"
REPO_URL="${REPO_URL:-https://github.com/phoughton/multiplayer_games.git}"
APP_BRANCH="${APP_BRANCH:-main}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_DIR="$SCRIPT_DIR/remote"
REMOTE_TMP="/tmp/pong-deploy"
SSH_TARGET="$DEPLOY_USER@$DEPLOY_HOST"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- preflight
if [ -z "${PETERHOUGHTONCOM_PAT:-}" ]; then
    cat >&2 <<EOF
error: PETERHOUGHTONCOM_PAT is not set.

Export your GitHub fine-grained PAT (read-only contents on
phoughton/multiplayer_games) and re-run:

  export PETERHOUGHTONCOM_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxx
  ./deploy/deploy.sh

Tip: prefix the 'export' with a space if HISTCONTROL=ignorespace is set,
     so the PAT doesn't hit your shell history.
EOF
    exit 2
fi

if [ ! -d "$PAYLOAD_DIR" ]; then
    fail "expected payload dir $PAYLOAD_DIR not found"
fi

bold "Pong deploy → $SSH_TARGET"
info "Domain      : $DEPLOY_DOMAIN (apex: $DEPLOY_APEX)"
info "Repo        : $REPO_URL @ $APP_BRANCH"
info "LE email    : $LETSENCRYPT_EMAIL"
info "Payload dir : $PAYLOAD_DIR"
echo

# ------------------------------------------------------------- ssh reachable
bold "[1/4] SSH reachability"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" true 2>/dev/null; then
    fail "cannot SSH to $SSH_TARGET (key-based auth). Check your agent and ~/.ssh/config."
fi
info "ok"
echo

# -------------------------------------------------------------- scp payload
bold "[2/4] Uploading deploy payload"
ssh "$SSH_TARGET" "rm -rf '$REMOTE_TMP' && install -d -m 700 '$REMOTE_TMP'"
# -p preserves perms for the executable install.sh / git-askpass.sh
scp -pq -r "$PAYLOAD_DIR"/. "$SSH_TARGET:$REMOTE_TMP/"
ssh "$SSH_TARGET" "chmod 700 '$REMOTE_TMP'/install.sh '$REMOTE_TMP'/git-askpass.sh"
info "ok"
echo

# --------------------------------------------------------- run remote installer
bold "[3/4] Running remote installer"
# The PAT is piped in via stdin so it never appears in `ps` argv or SSH logs,
# on either side. The remote shell reads one line into PETERHOUGHTONCOM_PAT,
# exports it, and execs install.sh which inherits the env.
printf '%s\n' "$PETERHOUGHTONCOM_PAT" | ssh -T "$SSH_TARGET" "
    set -eu
    IFS= read -r PETERHOUGHTONCOM_PAT
    export PETERHOUGHTONCOM_PAT
    export DOMAIN='$DEPLOY_DOMAIN'
    export APEX='$DEPLOY_APEX'
    export EMAIL='$LETSENCRYPT_EMAIL'
    export REPO_URL='$REPO_URL'
    export APP_BRANCH='$APP_BRANCH'
    bash '$REMOTE_TMP/install.sh'
"
echo

# ---------------------------------------------------------- health check
bold "[4/4] Post-deploy health check"
if curl -fsS --max-time 15 "https://$DEPLOY_DOMAIN/health"; then
    echo
    info "ok"
else
    echo
    fail "https://$DEPLOY_DOMAIN/health did not respond; check journalctl -u pong on the server"
fi
echo

bold "deploy complete"
