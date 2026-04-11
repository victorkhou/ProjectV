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
    """Thin wrapper over Evennia's channel/say/page infrastructure.

    Responsibilities:
    - Ensure the "Global" game channel exists on startup
    - Auto-subscribe players to the "Global" channel on login
    - Override channel message formatting to include player Rank
    - Override page (DM) formatting to include player Rank

    Delegates to:
    - Evennia's Channel system for global chat delivery
    - Evennia's built-in ``say`` command for local room chat
    - Evennia's built-in ``page`` command for direct messages
    """

    GLOBAL_CHANNEL_KEY = "Global"

    def __init__(self, channel_db_class: Any = None) -> None:
        """Initialize the chat system.

        Args:
            channel_db_class: Optional override for ChannelDB class
                (defaults to Evennia's ChannelDB). Useful for testing.
        """
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
        """Ensure the Global channel exists, creating it if necessary.

        Returns:
            The Global channel object, or None if ChannelDB is unavailable.
        """
        channel_db = self._get_channel_db()
        if channel_db is None:
            logger.log_err("ChatSystem: ChannelDB not available")
            return None

        try:
            channel, created = channel_db.objects.get_or_create(
                db_key=self.GLOBAL_CHANNEL_KEY,
                defaults={
                    "db_key": self.GLOBAL_CHANNEL_KEY,
                },
            )
            if created:
                logger.log_info(
                    f"ChatSystem: Created '{self.GLOBAL_CHANNEL_KEY}' channel"
                )
            return channel
        except Exception:
            logger.exception("ChatSystem: Error ensuring Global channel")
            return None

    def auto_subscribe(self, player: Any) -> None:
        """Auto-subscribe a player to the Global channel on login.

        Called from CombatCharacter.at_post_login().

        Args:
            player: The player character to subscribe.
        """
        channel_db = self._get_channel_db()
        if channel_db is None:
            return

        try:
            channel = channel_db.objects.get(db_key=self.GLOBAL_CHANNEL_KEY)
            if not channel.has_connection(player):
                channel.connect(player)
        except Exception:
            logger.exception("ChatSystem: Error auto-subscribing player")

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
