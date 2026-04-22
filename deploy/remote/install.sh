#!/usr/bin/env bash
# Idempotent installer for the Pong multiplayer server. Safe to re-run.
#
# Run as root on the target. Expected env (forwarded from deploy.sh):
#   PETERHOUGHTONCOM_PAT   GitHub PAT for the read-only clone.
#   DOMAIN                 www.peterhoughton.com
#   APEX                   peterhoughton.com
#   EMAIL                  Let's Encrypt registration email.
#   REPO_URL               https://github.com/phoughton/multiplayer_games.git
#   APP_BRANCH             main
#
# Payload (scp'd by the local driver into /tmp/pong-deploy/):
#   nginx.pong.conf  nginx.pong-limits.conf  pong.service  pong.env
#   fail2ban.jail.local  unattended-upgrades.conf  sshd_hardening.conf
#   git-askpass.sh

set -euo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${APEX:?APEX is required}"
: "${EMAIL:?EMAIL is required}"
: "${REPO_URL:?REPO_URL is required}"
: "${APP_BRANCH:?APP_BRANCH is required}"
: "${PETERHOUGHTONCOM_PAT:?PETERHOUGHTONCOM_PAT is required}"

export DEBIAN_FRONTEND=noninteractive

PAYLOAD_DIR="/tmp/pong-deploy"
APP_DIR="/opt/pong/app"
VENV_DIR="/opt/pong/venv"
LOG_DIR="/var/log/pong"
SVC_USER="pong"
ENV_FILE="/etc/pong.env"
NGINX_SITE="/etc/nginx/sites-available/pong"
NGINX_LINK="/etc/nginx/sites-enabled/pong"
NGINX_LIMITS="/etc/nginx/conf.d/pong-limits.conf"
UNIT_FILE="/etc/systemd/system/pong.service"
SSHD_DROPIN="/etc/ssh/sshd_config.d/00-pong-hardening.conf"
UA_FILE="/etc/apt/apt.conf.d/52unattended-upgrades-pong"
F2B_FILE="/etc/fail2ban/jail.local"

ts() { date +'%H:%M:%S'; }
log_ok()     { echo "[$(ts)] [ok]     $*"; }
log_change() { echo "[$(ts)] [change] $*"; }
log_skip()   { echo "[$(ts)] [skip]   $*"; }
log_step()   { echo; echo "== $(ts) :: $* =="; }

# --- exit trap: always remove transient secrets --------------------------
cleanup() {
    local rc=$?
    # Scrub the askpass helper and anything else secret-adjacent.
    rm -f "$PAYLOAD_DIR/git-askpass.sh" 2>/dev/null || true
    # Unset for future shells on this PID tree.
    unset PETERHOUGHTONCOM_PAT GIT_ASKPASS || true
    exit "$rc"
}
trap cleanup EXIT INT TERM

# ============================================================ 1. APT UPDATE
log_step "apt update + upgrade"
apt-get update -qq
apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
log_ok "system packages current"

# ====================================================== 2. BASE PACKAGES
log_step "base packages"
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    ufw fail2ban \
    unattended-upgrades apt-listchanges \
    git ca-certificates curl
log_ok "packages installed"

# ============================================= 3. UNATTENDED UPGRADES CFG
log_step "unattended-upgrades"
install -m 644 "$PAYLOAD_DIR/unattended-upgrades.conf" "$UA_FILE"
# Enable periodic updates (idempotent re-run).
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Download-Upgradeable-Packages "1";
EOF
systemctl enable --now unattended-upgrades.service >/dev/null
log_ok "unattended-upgrades active"

# =============================================== 4. SERVICE USER + DIRS
log_step "service user + dirs"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home /opt/pong --shell /usr/sbin/nologin "$SVC_USER"
    log_change "created user $SVC_USER"
else
    log_skip "user $SVC_USER already exists"
fi
install -d -o "$SVC_USER" -g "$SVC_USER" -m 755 /opt/pong
install -d -o "$SVC_USER" -g "$SVC_USER" -m 755 "$LOG_DIR"
install -d -m 755 /var/www/html

# ===================================================== 5. GIT PULL CODE
log_step "fetch app from GitHub"
chmod 700 "$PAYLOAD_DIR/git-askpass.sh"
export GIT_ASKPASS="$PAYLOAD_DIR/git-askpass.sh"
# Quiet git from offering to store the credential.
export GIT_TERMINAL_PROMPT=0

# The checkout is owned by `pong` but we are running as root, which trips
# git's "dubious ownership" guard. Scope the exception to this one path.
GIT="git -c safe.directory=$APP_DIR"

if [ ! -d "$APP_DIR/.git" ]; then
    rm -rf "$APP_DIR"
    $GIT clone --branch "$APP_BRANCH" --depth 50 "$REPO_URL" "$APP_DIR"
    log_change "cloned $REPO_URL@$APP_BRANCH"
else
    # Ensure origin matches; guard against drift.
    current_url=$($GIT -C "$APP_DIR" config --get remote.origin.url || echo "")
    if [ "$current_url" != "$REPO_URL" ]; then
        $GIT -C "$APP_DIR" remote set-url origin "$REPO_URL"
        log_change "reset origin URL to $REPO_URL"
    fi
    $GIT -C "$APP_DIR" fetch --prune --depth 50 origin "$APP_BRANCH"
    before=$($GIT -C "$APP_DIR" rev-parse HEAD)
    $GIT -C "$APP_DIR" reset --hard "origin/$APP_BRANCH"
    $GIT -C "$APP_DIR" clean -fd
    after=$($GIT -C "$APP_DIR" rev-parse HEAD)
    if [ "$before" = "$after" ]; then
        log_skip "already at $after"
    else
        log_change "advanced $before -> $after"
    fi
fi
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"

# ============================================================ 6. VENV
log_step "python venv + dependencies"
# Make sure pong owns /opt/pong itself (not just app/) so it can create venv.
chown "$SVC_USER:$SVC_USER" /opt/pong
chmod 755 /opt/pong
if [ ! -d "$VENV_DIR" ]; then
    sudo -u "$SVC_USER" python3 -m venv "$VENV_DIR"
    log_change "created venv at $VENV_DIR"
else
    log_skip "venv exists"
fi
sudo -u "$SVC_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$SVC_USER" "$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
log_ok "dependencies installed"

# ========================================================= 7. ENV FILE
log_step "env file"
install -m 640 -o root -g "$SVC_USER" "$PAYLOAD_DIR/pong.env" "$ENV_FILE"
log_ok "$ENV_FILE written (0640 root:$SVC_USER)"

# ===================================================== 8. SYSTEMD UNIT
log_step "systemd unit"
install -m 644 "$PAYLOAD_DIR/pong.service" "$UNIT_FILE"
systemctl daemon-reload
systemctl enable pong.service >/dev/null
# Restart so new code / env is picked up. If the service is broken, fail loud.
systemctl restart pong.service
sleep 1
if ! systemctl is-active --quiet pong.service; then
    echo "pong.service failed to start; recent logs:" >&2
    journalctl -u pong.service -n 50 --no-pager >&2 || true
    exit 1
fi
log_ok "pong.service active"

# =========================================================== 9. NGINX
log_step "nginx site"
install -m 644 "$PAYLOAD_DIR/nginx.pong-limits.conf" "$NGINX_LIMITS"
# Write the site file only if it is missing or differs AND is not the
# certbot-augmented variant (containing ssl_certificate). This keeps
# certbot's in-place edits across re-deploys.
write_site=true
if [ -f "$NGINX_SITE" ] && grep -q "ssl_certificate " "$NGINX_SITE"; then
    # Certbot has already personalised the file; leave it alone to preserve
    # the HTTPS blocks. Certbot will re-apply on next renew.
    write_site=false
    log_skip "nginx site already has TLS config (certbot-managed)"
fi
if $write_site; then
    install -m 644 "$PAYLOAD_DIR/nginx.pong.conf" "$NGINX_SITE"
    log_change "wrote $NGINX_SITE"
fi
ln -sfn "$NGINX_SITE" "$NGINX_LINK"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
log_ok "nginx reloaded"

# ===================================================== 10. LETS ENCRYPT
log_step "Let's Encrypt certificate"
has_cert=false
if certbot certificates 2>/dev/null | grep -F -q " $DOMAIN"; then
    has_cert=true
fi
if $has_cert; then
    certbot renew --quiet --deploy-hook "systemctl reload nginx" || true
    log_skip "certificate already present; ran renew"
else
    certbot --nginx --non-interactive --agree-tos \
        --email "$EMAIL" --no-eff-email \
        -d "$DOMAIN" -d "$APEX" \
        --redirect
    log_change "issued certificate for $DOMAIN,$APEX"
fi
systemctl enable --now certbot.timer >/dev/null || true

# ================================================== 11. UFW FIREWALL
log_step "ufw firewall"
ufw --force default deny incoming >/dev/null
ufw --force default allow outgoing >/dev/null
for p in 22/tcp 80/tcp 443/tcp; do
    if ! ufw status | grep -q "^$p "; then
        ufw allow "$p" >/dev/null
        log_change "allowed $p"
    else
        log_skip "$p already allowed"
    fi
done
ufw --force enable >/dev/null
log_ok "ufw active"

# ==================================================== 12. FAIL2BAN
log_step "fail2ban"
install -m 644 "$PAYLOAD_DIR/fail2ban.jail.local" "$F2B_FILE"
systemctl enable --now fail2ban >/dev/null
systemctl reload fail2ban 2>/dev/null || systemctl restart fail2ban
log_ok "fail2ban active"

# =================================================== 13. SSH HARDENING
log_step "sshd hardening"
install -m 644 "$PAYLOAD_DIR/sshd_hardening.conf" "$SSHD_DROPIN"
if ! sshd -t; then
    echo "sshd -t failed; refusing to reload sshd" >&2
    exit 1
fi
# Unit is named `ssh` on Debian/Ubuntu; use reload-or-restart for socket-
# activated setups on newer releases.
systemctl reload-or-restart ssh
log_ok "sshd reloaded with hardened config"

# ================================================== 14. HEALTH CHECK
log_step "local health check"
# The app's TrustedHostMiddleware rejects Host headers not in ALLOWED_HOSTS,
# so we pass the real domain via -H to satisfy the check on loopback.
for i in 1 2 3 4 5; do
    if curl -fsS -H "Host: $DOMAIN" "http://127.0.0.1:8000/health" >/dev/null; then
        log_ok "app responding on 127.0.0.1:8000/health"
        break
    fi
    sleep 1
    if [ "$i" = 5 ]; then
        echo "health check failed after 5 attempts" >&2
        journalctl -u pong.service -n 50 --no-pager >&2 || true
        exit 1
    fi
done

log_step "install.sh completed"
