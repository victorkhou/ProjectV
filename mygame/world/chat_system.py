"""
Chat System for the RTS Combat Overworld game.

A thin wrapper over Evennia's existing communication infrastructure.
Configures game channels on startup and overrides message formatting
to include player rank.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8
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

        try:
            channel = channel_db.objects.get(db_key=self.GLOBAL_CHANNEL_KEY)
            if not channel.has_connection(account):
                channel.connect(account)
            # Add 'chat' as a personal nick for the Public channel
            if hasattr(account, "nicks"):
                account.nicks.add("chat", self.GLOBAL_CHANNEL_KEY, category="channel")
        except Exception:
            logger.exception("ChatSystem: Error auto-subscribing account")

    def format_channel_message(self, sender: Any, message: str) -> str:
        """Format a channel message with the sender's rank.

        Format: "[{rank}] {name}: {message}"

        Args:
            sender: The player sending the message.
            message: The message text.

        Returns:
            Formatted message string.
        """
        name = getattr(sender, "key", "Unknown")
        rank = self._get_player_rank_name(sender)
        return f"[{rank}] {name}: {message}"

    def format_dm_message(self, sender: Any, message: str) -> str:
        """Format a direct message with the sender's rank.

        Format: "[{rank}] {name} (DM): {message}"

        Args:
            sender: The player sending the message.
            message: The message text.

        Returns:
            Formatted message string.
        """
        name = getattr(sender, "key", "Unknown")
        rank = self._get_player_rank_name(sender)
        return f"[{rank}] {name} (DM): {message}"

    @staticmethod
    def _get_player_rank_name(player: Any) -> str:
        """Get the rank name for a player.

        Tries multiple attribute patterns for flexibility.

        Args:
            player: The player character.

        Returns:
            The rank name string.
        """
        # Try rank_name attribute directly
        rank_name = getattr(player, "rank_name", None)
        if rank_name:
            return rank_name

        # Try traits-based rank level lookup
        rank_level = getattr(player, "rank_level", None)
        if rank_level is not None:
            # Would look up from DataRegistry in production
            return f"Rank {rank_level}"

        return "Recruit"
