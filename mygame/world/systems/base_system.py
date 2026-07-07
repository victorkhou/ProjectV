"""
Base class for world systems.

Every gameplay system (resource, combat, rank, tech, powerup, equipment,
building, agent) shares the same two collaborators: the :class:`DataRegistry`
(definitions + hot-tunable balance) and the :class:`EventBus` (publish/subscribe
for cross-system reactions). ``BaseSystem`` captures that shared contract in one
place so every system is constructed the same way and new systems have an
obvious, uniform starting point.

Systems that need extra collaborators (a tick clock, an object factory, a build
range, …) accept them as additional keyword arguments *after* calling
``super().__init__(registry, event_bus)`` — the base contract stays constant
while each system layers on what only it needs.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # avoid import cycles at runtime
    from world.core.ports.player_notifier import PlayerNotifier
    from world.data_registry import DataRegistry
    from world.event_bus import EventBus


class BaseSystem:
    """Common base for all world systems.

    Args:
        registry: The :class:`DataRegistry` holding definitions and the
            hot-tunable :class:`BalanceConfig`.
        event_bus: The :class:`EventBus` used to publish/subscribe to events.
        player_notifier: The :class:`PlayerNotifier` port used by
            :meth:`notify_player` to message a single player. Defaults to the
            Evennia adapter; tests inject a fake to capture messages.
    """

    def __init__(
        self,
        registry: "DataRegistry",
        event_bus: "EventBus",
        player_notifier: "PlayerNotifier | None" = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        from world.adapters.evennia_player_notifier import EvenniaPlayerNotifier

        self._player_notifier: "PlayerNotifier" = (
            player_notifier or EvenniaPlayerNotifier()
        )

    def notify_player(self, player: Any, message: str) -> None:
        """Send *message* to a single *player* via the injected notifier.

        Replaces the ``if hasattr(player, "msg"): player.msg(...)`` pattern in
        the domain: the adapter absorbs the None/missing-sink/error guarding, so
        systems just call ``self.notify_player(player, msg)``.
        """
        self._player_notifier.notify(player, message)
