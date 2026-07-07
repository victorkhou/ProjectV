"""
Shared test support for notification presentation.

``attach_presenter(event_bus)`` wires a real :class:`NotificationPresenter` to
a test's event bus so that ``system.notify(player, kind, ...)`` events flow
through the real format table and land on ``player.msg`` — the same path
production uses. Tests that assert on notification *content* use this instead
of the old "domain calls player.msg directly" behavior.

Not named ``test_*`` so pytest does not collect it as a test module.
"""

from __future__ import annotations

from typing import Any

from world.presenters.notification_presenter import NotificationPresenter


class _DirectPlayerNotifier:
    """Delivers to player.msg, mirroring EvenniaPlayerNotifier guarding."""

    def notify(self, player: Any, message: str) -> None:
        if player is None or not hasattr(player, "msg"):
            return
        try:
            player.msg(message)
        except Exception:
            pass


def attach_presenter(event_bus: Any) -> NotificationPresenter:
    """Subscribe a NotificationPresenter to *event_bus*; return it.

    The presenter delivers formatted lines to each player's ``msg`` sink, so a
    test capturing ``player.msg`` sees exactly the strings production would
    render for the emitted ``PLAYER_NOTIFICATION`` events.
    """
    return NotificationPresenter(event_bus, player_notifier=_DirectPlayerNotifier())
