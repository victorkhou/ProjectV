#!/usr/bin/env bash
#
# One-shot setup for the RTS Combat Overworld (Evennia) project on macOS/Linux.
#
# Idempotent bootstrap. Safe to re-run. It will:
#   1. Verify a compatible Python interpreter (>= 3.12, 3.14 recommended).
#   2. Create a virtualenv at .venv (if missing).
#   3. Install the vendored Evennia + deps in editable mode (pip install -e .).
#   4. Run the mygame first-time init (evennia --initmissing, evennia migrate).
#
# On the first `evennia migrate`, Evennia creates the "Account #1" superuser.
# By default this is an INTERACTIVE prompt (username / email / password), so run
# this script from a real terminal the first time. For non-interactive or CI
# use, set these environment variables and the superuser is created silently:
#     EVENNIA_SUPERUSER_USERNAME, EVENNIA_SUPERUSER_PASSWORD
#     (EVENNIA_SUPERUSER_EMAIL is optional)
#
# It intentionally does NOT run `evennia start` (that runs the live server in
# the foreground). After this script finishes, follow the printed next steps.
#
# Usage:
#   ./bootstrap.sh              # full setup + game init
#   ./bootstrap.sh --skip-init  # environment only, no --initmissing/migrate
#
#   # non-interactive (e.g. CI):
#   EVENNIA_SUPERUSER_USERNAME=admin EVENNIA_SUPERUSER_PASSWORD=change-me ./bootstrap.sh
#
set -euo pipefail

MIN_MAJOR=3
MIN_MINOR=12
RECOMMENDED_VERSION="3.14"

SKIP_INIT=0
for arg in "$@"; do
    case "$arg" in
        --skip-init) SKIP_INIT=1 ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# Resolve the repo root as the directory containing this script.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_EVENNIA="$VENV_DIR/bin/evennia"
GAME_DIR="$REPO_ROOT/mygame"

# Colours (fall back to no-op if not a tty).
if [ -t 1 ]; then
    C_CYAN="\033[36m"; C_GREEN="\033[32m"; C_YELLOW="\033[33m"; C_RESET="\033[0m"
else
    C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RESET=""
fi
step() { printf "${C_CYAN}==> %s${C_RESET}\n" "$1"; }
ok()   { printf "${C_GREEN}    %s${C_RESET}\n" "$1"; }
warn() { printf "${C_YELLOW}    %s${C_RESET}\n" "$1"; }

# ---------------------------------------------------------------------------
# 1. Find a compatible Python interpreter (only needed to build the venv).
# ---------------------------------------------------------------------------
version_ok() {
    # Args: <python-exe>. Echoes "MAJOR.MINOR" and returns 0 if >= floor.
    local exe="$1" ver maj min
    ver="$("$exe" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
    [ -n "$ver" ] || return 1
    maj="${ver%%.*}"; min="${ver##*.}"
    if [ "$maj" -gt "$MIN_MAJOR" ] || { [ "$maj" -eq "$MIN_MAJOR" ] && [ "$min" -ge "$MIN_MINOR" ]; }; then
        echo "$ver"; return 0
    fi
    return 1
}

PYTHON=""
PYVER=""
step "Checking for Python >= $MIN_MAJOR.$MIN_MINOR (recommended $RECOMMENDED_VERSION)..."
for candidate in "python$RECOMMENDED_VERSION" python3.14 python3.13 python3.12 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if v="$(version_ok "$candidate")"; then
            PYTHON="$candidate"; PYVER="$v"; break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "No compatible Python interpreter found on PATH."
    echo ""
    echo "Install Python $RECOMMENDED_VERSION, then re-run this script:"
    echo "  - macOS (Homebrew): brew install python@3.14"
    echo "  - Debian/Ubuntu:    sudo apt install python3.14 python3.14-venv"
    echo "  - Or download:      https://www.python.org/downloads/"
    exit 1
fi
ok "Using $PYTHON -> Python $PYVER"

# ---------------------------------------------------------------------------
# 2. Create the virtualenv.
# ---------------------------------------------------------------------------
if [ -x "$VENV_PYTHON" ]; then
    step "Virtualenv already exists at .venv (reusing)."
else
    step "Creating virtualenv at .venv..."
    "$PYTHON" -m venv "$VENV_DIR"
    [ -x "$VENV_PYTHON" ] || { echo "venv creation failed: $VENV_PYTHON not found." >&2; exit 1; }
    ok "Created .venv"
fi

# ---------------------------------------------------------------------------
# 3. Install the project (vendored Evennia + deps) in editable mode.
# ---------------------------------------------------------------------------
step "Upgrading pip..."
"$VENV_PYTHON" -m pip install --upgrade pip >/dev/null

step "Installing project dependencies (pip install -e .) - this can take a few minutes..."
"$VENV_PYTHON" -m pip install -e "$REPO_ROOT"

# Sanity-check: import must resolve to the vendored copy, not a PyPI Evennia.
RESOLVED="$("$VENV_PYTHON" -c 'import evennia; print(evennia.__file__)')"
case "$RESOLVED" in
    "$REPO_ROOT/evennia"*) ok "evennia resolves to the vendored copy." ;;
    *)
        warn "WARNING: 'import evennia' resolved to $RESOLVED"
        warn "Expected it under $REPO_ROOT/evennia (the vendored copy). Check for a stray pip-installed evennia."
        ;;
esac
"$VENV_EVENNIA" --version

# ---------------------------------------------------------------------------
# 4. First-time game init (from mygame/).
# ---------------------------------------------------------------------------
if [ "$SKIP_INIT" -eq 1 ]; then
    step "Skipping game init (--skip-init)."
else
    step "Initialising the game in mygame/ (--initmissing, migrate)..."
    if [ -z "${EVENNIA_SUPERUSER_USERNAME:-}" ]; then
        warn "First run: 'migrate' will prompt you to create the Account #1 superuser."
        warn "(Set EVENNIA_SUPERUSER_USERNAME/PASSWORD to do this non-interactively.)"
    fi
    ( cd "$GAME_DIR" && "$VENV_EVENNIA" --initmissing && "$VENV_EVENNIA" migrate )
    ok "Game initialised."
fi

# ---------------------------------------------------------------------------
# Done - print next steps.
# ---------------------------------------------------------------------------
echo ""
step "Setup complete. Next steps:"
echo "  1. Activate the venv:   source .venv/bin/activate"
echo "  2. Go to the game dir:  cd mygame"
echo "  3. Start the server:    evennia start"
echo "     (if you skipped superuser creation above, start will prompt for it)"
echo ""
echo "  Connect: telnet localhost 4000  |  web client: http://localhost:4001"
