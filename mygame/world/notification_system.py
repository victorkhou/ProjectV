"""
Notification System for the RTS Combat Overworld game.

Subscribes to game events and broadcasts formatted messages to all
connected sessions via Evennia's SESSION_HANDLER.

"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from world.constants import UNIT_KIND_LABELS
from world.event_bus import (
    EventBus,
    PLAYER_LOGIN,
    PLAYER_LOGOUT,
    PLAYER_ELIMINATED,
    RANK_PROMOTED,
    RANK_DEMOTED,
)

if TYPE_CHECKING:
    from world.core.ports.notifier import Notifier

logger = logging.getLogger("evennia")


class NotificationSystem:
    """Subscribes to game events and sends global notifications.

    Args:
        event_bus: The EventBus to subscribe to.
        notifier: The :class:`Notifier` port used to broadcast. Defaults to the
            Evennia adapter; tests inject a fake that records messages instead
            of monkeypatching the module-level ``world.utils.broadcast``.
    """

    def __init__(
        self, event_bus: EventBus, notifier: "Notifier | None" = None, **kwargs
    ) -> None:
        self.event_bus = event_bus
        from world.adapters.evennia_notifier import EvenniaNotifier

        self._notifier: "Notifier" = notifier or EvenniaNotifier()
        self._subscribe()

    def _subscribe(self) -> None:
        """Subscribe to relevant game events."""
        self.event_bus.subscribe(PLAYER_LOGIN, self.on_player_login)
        self.event_bus.subscribe(PLAYER_LOGOUT, self.on_player_logout)
        self.event_bus.subscribe(PLAYER_ELIMINATED, self.on_player_eliminated)
        self.event_bus.subscribe(RANK_PROMOTED, self.on_rank_promoted)
        self.event_bus.subscribe(RANK_DEMOTED, self.on_rank_demoted)

    def _broadcast(self, message: str) -> None:
        """Send a tagged message to all connected players via the notifier."""
        self._notifier.broadcast(message)

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
        attacker_owner: Any = None,
        attacker_kind: str = "",
        **kwargs,
    ) -> None:
        """Broadcast elimination notification, attributed to the owning player.

        A kill by A's turret or A's agent is announced as "Player A's Turret has
        eliminated Player B" — the owning player (``attacker_owner``) is the
        named killer, with ``attacker_kind`` ('turret'/'agent'/'building')
        supplying the possessive unit suffix. A direct player kill (no unit
        kind, or owner == attacker) reads plainly "Player A has eliminated ...".
        """
        killer = attacker_owner if attacker_owner is not None else attacker
        killer_name = getattr(killer, "key", "Unknown") if killer else "Unknown"
        victim_name = getattr(victim, "key", "Unknown") if victim else "Unknown"

        subject = f"{killer_name}{self._unit_suffix(killer, attacker, attacker_kind)}"
        self._broadcast(
            f"|y[Combat] {subject} has eliminated {victim_name}!|n"
        )

    @staticmethod
    def _unit_suffix(killer: Any, attacker: Any, attacker_kind: str) -> str:
        """Return a possessive unit suffix (" 's Turret") or '' for a direct kill.

        Empty when the killer struck directly (the attacker IS the owner, or no
        unit kind was supplied); otherwise names the unit that scored the kill.
        """
        if attacker is killer or not attacker_kind:
            return ""
        return f"'s {UNIT_KIND_LABELS.get(attacker_kind, 'unit')}"

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
