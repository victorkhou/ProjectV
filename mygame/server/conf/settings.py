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

# Do NOT auto-puppet the last character on login. With this off, a connecting
# account lands at the character-select screen and must explicitly choose which
# character to play (``ic <name>``) rather than being dropped straight into the
# last-played puppet. (Evennia: disable AUTO_PUPPET_ON_LOGIN for a char-select
# screen on login.) The lifecycle spawning/lobby flow then runs on the puppet
# the player selects, via CombatCharacter.at_post_puppet.
AUTO_PUPPET_ON_LOGIN = False

# Player lobby / spawning lifecycle flow (states 3-4 of the player state
# machine). ON: world commands are gated behind a PLAYING state, every login
# routes through spawning/lobby, and death/disconnect reroute through the
# staging flow (re-pick class + spawn, then 'enter'). Set to False to revert to
# the legacy behavior (instant game entry on login, instant in-place respawn).
LOBBY_FLOW_ENABLED = True

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