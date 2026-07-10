#!/usr/bin/env bash
# Run the live-boot smoke test against REAL Evennia + a Django in-memory test DB.
#
# This test is skipped by the normal (stubbed) `pytest mygame` run — it needs the
# real framework, so it runs in its own process with the stub escape hatch set.
#
# Usage:
#   mygame/tests/run_live_boot_smoke.sh
#
# Must be run from the mygame/ package directory (or it will cd there).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # -> mygame/
cd "$HERE"

EVENNIA_REAL_BOOT=1 \
DJANGO_SETTINGS_MODULE=server.conf.settings \
    python -m pytest tests/test_live_boot_smoke.py -q -p no:cacheprovider "$@"
