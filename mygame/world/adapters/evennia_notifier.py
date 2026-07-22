"""
Evennia-backed :class:`Notifier` implementation.

Wraps ``evennia.SESSION_HANDLER`` so the domain can broadcast without importing
Evennia. This is the single home for the session-iteration I/O behind
``world.utils.broadcast``.
"""

from __future__ import annotations

import logging

from world.core.ports.notifier import Notifier

logger = logging.getLogger("evennia.world.adapters.notifier")


class EvenniaNotifier(Notifier):
    """Broadcasts to all connected accounts via Evennia's session handler."""

    def broadcast(self, message: str, cls: str = "game-chat") -> None:
        try:
            from evennia import SESSION_HANDLER

            for session in SESSION_HANDLER.get_sessions():
                account = session.get_account()
                if account and hasattr(account, "msg"):
                    account.msg(text=(message, {"cls": cls}))
        except Exception:
            logger.exception("broadcast failed")
