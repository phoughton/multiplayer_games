# Deploy

Idempotent deployment of the Pong multiplayer server to a Hetzner Ubuntu box at `178.104.213.233`, served at `https://www.peterhoughton.com`.

## One-time setup

1. **SSH key** — your public key must be in `root@178.104.213.233:~/.ssh/authorized_keys` (Hetzner seeds this for you at provisioning time).
2. **DNS** — point both `www.peterhoughton.com` and `peterhoughton.com` A records at `178.104.213.233`. Wait for propagation (TTL + ≤5 min) before running the deploy; Let's Encrypt needs the domains to resolve to the box for HTTP-01.
3. **GitHub PAT** — generate a fine-grained PAT on github.com with:
   - Resource owner: `phoughton`
   - Selected repository: `multiplayer_games`
   - Repository permissions → Contents: **Read-only**
   - Expiration: 90 days (or your preferred rotation window)

## Run

```bash
export PETERHOUGHTONCOM_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
./deploy/deploy.sh
```

Prefix the `export` with a literal space if your shell has `HISTCONTROL=ignorespace` (default on most distros) so the PAT doesn't land in `~/.bash_history`.

The driver:

1. Checks SSH reachability.
2. scps `deploy/remote/*` to `/tmp/pong-deploy/` on the server.
3. Runs `install.sh` remotely, streaming output back. The PAT is piped in via stdin — it never appears in `ps`, SSH logs, or on disk beyond the in-memory env of `install.sh`.
4. Curls `https://www.peterhoughton.com/health` as a smoke test.

## What the installer does (all idempotent)

1. `apt-get update && upgrade`
2. Install: python3 + venv, nginx, certbot+plugin, ufw, fail2ban, unattended-upgrades, git, curl.
3. Enable automatic security patches (3:30 am reboots permitted).
4. Create system user `pong` (no shell).
5. `git clone` or `git fetch + reset --hard` the repo to `/opt/pong/app`. Uses a transient `GIT_ASKPASS` helper so the PAT never reaches `.git/config` or `~/.git-credentials`.
6. Create venv at `/opt/pong/venv` and install `requirements.txt`.
7. Write `/etc/pong.env` (0640 root:pong).
8. Install systemd unit `pong.service` (hardened: `NoNewPrivileges`, `ProtectSystem=strict`, restricted syscalls, memory write-exec denied, …). `daemon-reload` + `restart`.
9. Install nginx site (with full security-header set) + rate-limit zone. `nginx -t` before reload.
10. Issue or renew Let's Encrypt cert for `www.peterhoughton.com` and `peterhoughton.com` via the nginx plugin; enable `certbot.timer` for auto-renewal.
11. Configure ufw: deny all incoming, allow 22/80/443. Enable.
12. Install fail2ban jails for sshd + nginx. Enable.
13. Drop in `/etc/ssh/sshd_config.d/00-pong-hardening.conf` (key-only root, no passwords, etc). `sshd -t` then `systemctl reload ssh`.
14. `curl` the app's local `/health`. Fail the deploy if it doesn't answer.

SSH hardening runs **last** so a bad sshd config can't lock you out mid-deploy.

## Environment variables (all optional; defaults shown)

| Var | Default | Purpose |
| --- | --- | --- |
| `PETERHOUGHTONCOM_PAT` | **required** | GitHub PAT for the read-only clone. |
| `DEPLOY_HOST` | `178.104.213.233` | Target server. |
| `DEPLOY_USER` | `root` | SSH user. |
| `DEPLOY_DOMAIN` | `www.peterhoughton.com` | Primary cert SAN; served here. |
| `DEPLOY_APEX` | `peterhoughton.com` | Secondary cert SAN; 301s to www. |
| `LETSENCRYPT_EMAIL` | `pete@investigatingsoftware.co.uk` | Cert registration / expiry emails. |
| `REPO_URL` | `https://github.com/phoughton/multiplayer_games.git` | Origin to clone. |
| `APP_BRANCH` | `main` | Branch to deploy. Set to a tag/SHA to pin. |

## Rolling back

No blue/green. To roll back to a previous version:

```bash
# Pin the branch to a known-good commit, then deploy.
APP_BRANCH=<sha-or-tag> ./deploy/deploy.sh
```

Or SSH in and pin directly:

```bash
ssh root@178.104.213.233 'git -C /opt/pong/app checkout <sha> && systemctl restart pong'
```

A subsequent `./deploy/deploy.sh` with the default `APP_BRANCH=main` will re-advance to the latest commit — `git reset --hard + clean -fd` overwrites any local pin.

## Troubleshooting

**"PETERHOUGHTONCOM_PAT is not set"** — you forgot to export the PAT. See "Run" above.

**`ssh: connect: Connection refused`** — the box is rebooting (unattended-upgrades allows 3:30 am reboots) or you're blocked by fail2ban. `ssh root@178.104.213.233` from another IP and `fail2ban-client unban <your-ip>`.

**Certbot fails with "DNS problem: NXDOMAIN"** — your DNS A records haven't propagated. Check with `dig www.peterhoughton.com +short`.

**App healthcheck fails** — SSH in and `journalctl -u pong -n 100 --no-pager`. Most often an env/config issue in `/etc/pong.env` or a missing Python dep.

**Nginx won't reload** — `nginx -t` locally on the server to see the syntax error. If certbot crashed mid-rewrite the site file may be in an odd state: `install -m 644 /tmp/pong-deploy/nginx.pong.conf /etc/nginx/sites-available/pong` and re-run the deploy to re-issue via `--nginx` + `--redirect`.

## Verifying a deploy

```bash
curl -fsS https://www.peterhoughton.com/health
curl -sI https://www.peterhoughton.com/ | grep -iE 'strict-transport|x-frame|content-security-policy'
curl -sI http://peterhoughton.com/      # expect 301 -> https://www.peterhoughton.com/
```

On the box:

```bash
systemctl is-active pong nginx fail2ban
ufw status verbose
certbot certificates
sshd -T | grep -E 'permitrootlogin|passwordauthentication'
```

## Re-running on a PAT-less server

The PAT is not stored, so every deploy needs it. If you've rotated or lost the PAT, generate a new one (see "One-time setup" step 3) and rerun.

## Files in this folder

```
deploy/
  deploy.sh                         # Local driver (run this)
  remote/
    install.sh                      # Idempotent installer (runs as root on target)
    git-askpass.sh                  # Transient GH credential helper
    nginx.pong.conf                 # Site config (pre-cert)
    nginx.pong-limits.conf          # rate-limit zone, in /etc/nginx/conf.d/
    pong.service                    # Hardened systemd unit
    pong.env                        # Env file template
    fail2ban.jail.local             # sshd + nginx jails
    unattended-upgrades.conf        # Automatic security patches
    sshd_hardening.conf             # Key-only root, no passwords, etc.
```
