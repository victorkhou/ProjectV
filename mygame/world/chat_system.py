"""
Chat System for the RTS Combat Overworld game.

A thin wrapper over Evennia's existing communication infrastructure.
Configures game channels on startup. Message formatting (the rank-prefixed
lines) lives in :mod:`world.utils` (``format_channel_message`` /
``format_dm_message``), the single source of truth used by production.

"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("evennia")


class ChatSystem:
    """Channel management for the RTS Combat Overworld.

    Responsibilities:
    - Verify the Public channel exists on startup
    - Auto-subscribe accounts to the Public channel on login
    - Set up 'chat' nick alias for the Public channel

    Message formatting is handled by Account.at_pre_channel_msg.
    Message tagging is handled by Account.channel_msg.
    """

    GLOBAL_CHANNEL_KEY = "Public"

    def __init__(self, channel_db_class: Any = None) -> None:
        self._channel_db_class = channel_db_class

    def _get_channel_db(self) -> Any:
        """Get the ChannelDB class (or the injected test seam), or None."""
        from world.channel_utils import get_channel_db
        return get_channel_db(self._channel_db_class)

    def ensure_global_channel(self) -> Any:
        """Ensure the Public channel exists (Evennia creates it by default).

        Returns the channel object or None.
        """
        channel_db = self._get_channel_db()
        if channel_db is None:
            return None
        try:
            return channel_db.objects.get(db_key=self.GLOBAL_CHANNEL_KEY)
        except Exception:
            logger.info("ChatSystem: Public channel not found — Evennia should create it on boot.")
            return None

    def auto_subscribe(self, account: Any) -> None:
        """Auto-subscribe an account to the Public channel and set up aliases.

        Adds 'chat' as a personal alias so players can type 'chat Hello'.
        """
        channel_db = self._get_channel_db()
        if channel_db is None:
            return

        # Connect the account in a DB savepoint (channel_utils isolates the write
        # so a missing Public channel can't poison the surrounding transaction),
        # then add the personal 'chat' nick alias.
        from world.channel_utils import subscribe_account
        subscribe_account(account, self.GLOBAL_CHANNEL_KEY, channel_db=channel_db)
        try:
            if hasattr(account, "nicks"):
                account.nicks.add("chat", self.GLOBAL_CHANNEL_KEY, category="channel")
        except Exception:
            logger.exception("ChatSystem: Error adding chat nick alias")
