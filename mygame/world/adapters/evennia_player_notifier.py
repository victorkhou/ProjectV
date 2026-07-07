"""
Evennia-backed :class:`PlayerNotifier` implementation.

Sends a message to a single player via its Evennia ``msg`` sink, absorbing the
defensive guarding (``None`` player, missing ``msg``, transport errors) that
was previously repeated inline at every domain call site.
"""

from __future__ import annotations

import logging
from typing import Any

from world.core.ports.player_notifier import PlayerNotifier

logger = logging.getLogger("evennia.world.adapters.player_notifier")


class EvenniaPlayerNotifier(PlayerNotifier):
    """Delivers a message to one player via its ``msg`` method."""

    def notify(self, player: Any, message: str) -> None:
        if player is None or not hasattr(player, "msg"):
            return
        try:
            player.msg(message)
        except Exception:
            logger.exception("player notify failed for %r", player)
