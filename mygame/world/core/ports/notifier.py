"""
Outbound-notification port.

Decouples the domain from the delivery transport: a use case that wants to tell
every connected player something depends on this abstraction, not on Evennia's
``SESSION_HANDLER``. The Evennia implementation lives in
``world.adapters.evennia_notifier``; tests inject a fake that records messages.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Notifier(ABC):
    """Broadcasts messages to connected players over some transport."""

    @abstractmethod
    def broadcast(self, message: str, cls: str = "game-chat") -> None:
        """Send *message* to every connected player.

        Args:
            message: The text to send.
            cls: CSS class hint for webclient routing.
        """
