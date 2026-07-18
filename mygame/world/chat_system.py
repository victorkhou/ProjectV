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
        """Get the ChannelDB class, importing lazily if needed."""
        if self._channel_db_class is not None:
            return self._channel_db_class
        try:
            from evennia.comms.models import ChannelDB
            return ChannelDB
        except ImportError:
            return None

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

        # Isolate the channel/nick writes in a DB savepoint: if the Public
        # channel is missing (e.g. a minimal test DB) the failing query would
        # otherwise poison the surrounding transaction and cascade to unrelated
        # queries. A savepoint rolls back ONLY this block's writes on failure.
        try:
            from django.db import transaction
            with transaction.atomic():
                channel = channel_db.objects.get(db_key=self.GLOBAL_CHANNEL_KEY)
                if not channel.has_connection(account):
                    channel.connect(account)
                # Add 'chat' as a personal nick for the Public channel
                if hasattr(account, "nicks"):
                    account.nicks.add("chat", self.GLOBAL_CHANNEL_KEY, category="channel")
        except Exception:
            logger.exception("ChatSystem: Error auto-subscribing account")
