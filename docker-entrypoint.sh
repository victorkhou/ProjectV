#!/usr/bin/env bash
#
# Container entrypoint for the RTS Combat Overworld game.
#
# Runs the first-time game init (idempotent) and then hands off to the given
# command (default: `evennia start -l`, which runs the server in the foreground
# so the container stays alive and streams logs to `docker logs`).
#
# The Account #1 superuser is created on the first migrate. In a container there
# is usually no interactive TTY, so set these to create it without prompting:
#     EVENNIA_SUPERUSER_USERNAME, EVENNIA_SUPERUSER_PASSWORD
# (docker-compose.yml sets development defaults - change them for anything real.)
#
set -e

GAME_DIR=/usr/src/projectv/mygame
cd "$GAME_DIR"

# Remove leftover pid/restart files from an unclean previous shutdown, so the
# launcher doesn't think the server is still running.
rm -f server/*.pid server/*.restart >/dev/null 2>&1 || true

# Create missing local files (secret_settings.py, logs dir) - safe to re-run.
evennia --initmissing

# Apply database migrations. On the very first run this also creates the
# Account #1 superuser (non-interactively if EVENNIA_SUPERUSER_* are set).
evennia migrate

echo "==> Game initialised. Launching: $*"
exec "$@"
