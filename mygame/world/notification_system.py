"""
Notification System for the RTS Combat Overworld game.

Subscribes to game events and broadcasts formatted messages to all
connected sessions via Evennia's SESSION_HANDLER.

Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 28.4
"""

from __future__ import annotations

import logging
from typing import Any

from world.event_bus import (
    EventBus,
    PLAYER_LOGIN,
    PLAYER_LOGOUT,
    PLAYER_ELIMINATED,
    RANK_PROMOTED,
    RANK_DEMOTED,
)

logger = logging.getLogger("evennia")


class NotificationSystem:
    """Subscribes to game events and sends global notifications."""

    def __init__(self, event_bus: EventBus, **kwargs) -> None:
        self.event_bus = event_bus
        self._subscribe()

    def _subscribe(self) -> None:
        """Subscribe to relevant game events."""
        self.event_bus.subscribe(PLAYER_LOGIN, self.on_player_login)
        self.event_bus.subscribe(PLAYER_LOGOUT, self.on_player_logout)
        self.event_bus.subscribe(PLAYER_ELIMINATED, self.on_player_eliminated)
        self.event_bus.subscribe(RANK_PROMOTED, self.on_rank_promoted)
        self.event_bus.subscribe(RANK_DEMOTED, self.on_rank_demoted)

    @staticmethod
    def _broadcast(message: str) -> None:
        """Send a tagged message to all connected players."""
        from world.utils import broadcast
        broadcast(message)

    # ------------------------------------------------------------------ #
    #  Event handlers
    # ------------------------------------------------------------------ #

    def on_player_login(self, event_name: str = "", player: Any = None, **kwargs) -> None:
        """Broadcast login notification."""
        name = getattr(player, "key", "Unknown") if player else "Unknown"
        self._broadcast(f"|g[Server] {name} has logged in.|n")

    def on_player_logout(self, event_name: str = "", player: Any = None, **kwargs) -> None:
        """Broadcast logout notification."""
        name = getattr(player, "key", "Unknown") if player else "Unknown"
        self._broadcast(f"|r[Server] {name} has logged out.|n")

    def on_player_eliminated(
        self,
        event_name: str = "",
        attacker: Any = None,
        victim: Any = None,
        **kwargs,
    ) -> None:
        """Broadcast elimination notification."""
        attacker_name = getattr(attacker, "key", "Unknown") if attacker else "Unknown"
        victim_name = getattr(victim, "key", "Unknown") if victim else "Unknown"
        self._broadcast(
            f"|y[Combat] {attacker_name} has eliminated {victim_name}!|n"
        )

    def on_rank_promoted(
        self,
        event_name: str = "",
        player: Any = None,
        old_rank: Any = None,
        new_rank: Any = None,
        **kwargs,
    ) -> None:
        """Broadcast promotion notification."""
        name = getattr(player, "key", "Unknown") if player else "Unknown"
        rank_name = getattr(new_rank, "name", str(new_rank)) if new_rank else "Unknown"
        self._broadcast(
            f"|c[Rank] {name} has been promoted to {rank_name}!|n"
        )

    def on_rank_demoted(
        self,
        event_name: str = "",
        player: Any = None,
        old_rank: Any = None,
        new_rank: Any = None,
        **kwargs,
    ) -> None:
        """Broadcast demotion notification."""
        name = getattr(player, "key", "Unknown") if player else "Unknown"
        rank_name = getattr(new_rank, "name", str(new_rank)) if new_rank else "Unknown"
        self._broadcast(
            f"|r[Rank] {name} has been demoted to {rank_name}.|n"
        )
