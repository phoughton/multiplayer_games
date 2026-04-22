#!/usr/bin/env bash
# Transient credential helper for git-over-HTTPS. Exports `oauth2` as the
# username and the forwarded $PETERHOUGHTONCOM_PAT as the password. Never
# reaches disk except via `install -m 700` to /tmp/pong-deploy/, and is
# unlinked by install.sh's exit trap at the end of the run.
#
# Git will exec this twice per remote call:
#   $1 == "Username for 'https://github.com': " -> print "oauth2"
#   $1 == "Password for 'https://oauth2@github.com': " -> print the PAT
case "$1" in
    Username*) echo "oauth2" ;;
    Password*) echo "${PETERHOUGHTONCOM_PAT:?git-askpass: PETERHOUGHTONCOM_PAT is not set}" ;;
    *) exit 1 ;;
esac
