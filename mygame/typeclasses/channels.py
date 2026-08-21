"""
Channel typeclass — the OOC chat rooms Accounts subscribe to.

Sending is driven by the CMD_CHANNEL syscommand (see ``evennia.syscmds``) and
needs no override here.
"""

from evennia.comms.comms import DefaultChannel


class Channel(DefaultChannel):
    """Base class for all channel comms.

    Unmodified from ``DefaultChannel`` — the project's channel behaviour lives
    in :class:`typeclasses.accounts.Account` (rank prefixing, webclient tagging)
    and ``world.chat_system`` (auto-subscription). See the Evennia autodocs for
    the inherited API and hooks.
    """

    pass
