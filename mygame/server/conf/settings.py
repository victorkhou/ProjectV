r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
SERVERNAME = "mygame"

# Use CombatCharacter as the default character typeclass
BASE_CHARACTER_TYPECLASS = "typeclasses.characters.CombatCharacter"

# Disable the EvMore help pager. Long help entries otherwise show a paginated
# footer — "(Page [1/2] next | previous | top | end | quit)" — whose control
# keys (n/p/q/…) are aliases on a single pager command that can end up live from
# two cmdset sources at once, so typing 'q' triggers a confusing multi-match
# ("q-1 / q-2"). Our clients scroll inline (the webclient's output panel, and
# any standard terminal), and help text is now authored to wrap to the client
# width, so paging adds nothing here. With this off, help prints in full and the
# client handles scrolling.
HELP_MORE_ENABLED = False

# Player lobby / spawning lifecycle flow (states 3-4 of the player state
# machine). ON: world commands are gated behind a PLAYING state, every login
# routes through spawning/lobby, and death/disconnect reroute through the
# staging flow (re-pick class + spawn, then 'enter'). Set to False to revert to
# the legacy behavior (instant game entry on login, instant in-place respawn).
LOBBY_FLOW_ENABLED = True

# --- Session model the lifecycle flow depends on -------------------------- #
# The staging flow (lobby/spawning) is built around Evennia AUTO-PUPPETING a
# SINGLE character on login: at_post_puppet fires and drops the player straight
# into the lobby menu / spawning wizard (announce_lobby / announce_spawning)
# rather than an OOC character-select screen. These are Evennia's defaults, but
# we pin them EXPLICITLY so a future edit can't silently break the flow (e.g.
# flipping to multi-character select would land players at OOC char-select, and
# at_post_puppet would not route them as designed). Authentication is unaffected:
# a connection still always requires username + password; auto-puppet only skips
# the 'ic <name>' character-select step. See .kiro/specs/player-lifecycle
# (Requirement 12). Changing any of these requires revisiting the lifecycle
# login/quit routing and its multi-puppet handling.
AUTO_PUPPET_ON_LOGIN = True
MULTISESSION_MODE = 0
MAX_NR_CHARACTERS = 1

# --- Require an explicit login on every new session ----------------------- #
# By default Evennia auto-logs-in a new webclient connection from a shared
# browser-session cookie: once any tab has authenticated, a new webclient session
# silently logs in as that same account with no connect screen — and because
# MULTISESSION_MODE=0 disconnects duplicate sessions on login, opening a new tab
# USURPS the character already playing on another session. We want every new
# session to require explicit credentials, so we disable BOTH cookie writers:
#
#   1. Drop SharedLoginMiddleware here (shares a *website* login into the
#      webclient via the request/response cycle).
#   2. Neutralize the webclient protocols' own at_login cookie write — done in
#      server/conf/portal_services_plugins.py (a startup monkeypatch, which
#      avoids the settings-path circular import the AJAX protocol class body
#      would otherwise trigger).
#
# Trade-off: the shared cookie (webclient_authenticated_uid) is the ONLY thing
# that re-authenticates a REOPENED webclient socket (Evennia webclient.py onOpen
# / webclient_ajax.py mode_init gate the whole "already logged in" block on it).
# Disabling it means EVERY new socket — a new tab AND a page reload of an
# existing tab — lands at the login screen and must re-enter credentials. There
# is no way to allow reload-reconnect while blocking new-tab usurpation, because
# both are just "a fresh socket reading the same browser cookie." We accept the
# reload cost to close the usurpation hole. (An in-place live socket that never
# closes — e.g. the websocket staying open — is unaffected; only a NEW socket
# re-authenticates.)
MIDDLEWARE = [
    m for m in MIDDLEWARE
    if m != "evennia.web.utils.middleware.SharedLoginMiddleware"
]

# Add 'chat' as a server-level alias for the Public channel
DEFAULT_CHANNELS = [
    {
        "key": "Public",
        "aliases": ("pub", "chat"),
        "desc": "Public discussion",
        "locks": "control:perm(Admin);listen:all();send:all()",
    }
]


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")


try:
    # Created by the `evennia connections` wizard
    from .connection_settings import *
except ImportError:
    pass