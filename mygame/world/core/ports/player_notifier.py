"""
Per-player notification port.

Where :class:`~world.core.ports.notifier.Notifier` broadcasts to *everyone*,
this port delivers a message to a *single* player entity. It lets use-case
systems tell one player something without calling ``player.msg(...)`` directly
— removing both the transport coupling and the repeated defensive
``if hasattr(player, "msg")`` guard from the domain, behind one seam a test can
capture or an alternate transport can replace.

The Evennia implementation is
``world.adapters.evennia_player_notifier.EvenniaPlayerNotifier``; it performs
the ``hasattr``/``try`` guarding so callers stay clean.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PlayerNotifier(ABC):
    """Delivers a message to a single player entity."""

    @abstractmethod
    def notify(self, player: Any, message: str) -> None:
        """Send *message* to *player*.

        Implementations must be tolerant: a ``None`` player, or one without a
        working message sink, is a silent no-op (never raises into the caller).
        """
