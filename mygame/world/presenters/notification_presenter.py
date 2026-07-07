"""
NotificationPresenter — formats and delivers player-facing notifications.

Subscribes to the ``PLAYER_NOTIFICATION`` event that domain systems emit and is
the single owner of the per-player message strings that used to live inline in
the systems. Each event carries ``player``, ``kind``, and a ``data`` dict; the
presenter looks the ``kind`` up in its format table, builds the line, and
delivers it via the injected :class:`PlayerNotifier`.

Adding or restyling a player message is now a one-line change to
``_FORMATTERS`` here, with no edit to the use-case systems.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from world.event_bus import EventBus, PLAYER_NOTIFICATION

logger = logging.getLogger("evennia.world.presenters.notification")


def _fmt_rank_level_up(d: dict) -> str:
    return f"You are now Level {d['level']} ({d['rank_name']} {d['sub']})"


def _fmt_building_progress(d: dict) -> str:
    if d.get("target_level"):
        return (
            f"|y[Building] Upgrading {d['btype']} to L{d['target_level']}... "
            f"{d['progress']}/{d['total']}s ({d['remaining']}s remaining)|n"
        )
    return (
        f"|y[Building] Constructing {d['btype']}... "
        f"{d['progress']}/{d['total']}s ({d['remaining']}s remaining)|n"
    )


def _fmt_building_complete(d: dict) -> str:
    if d.get("target_level"):
        return f"|g[Complete] {d['building_type']} upgraded to level {d['target_level']}!|n"
    return (
        f"|g[Complete] {d['building_type']} construction finished! "
        f"The building is now operational.|n"
    )


def _fmt_agent_training_complete(d: dict) -> str:
    aid = d["agent_id"]
    return (
        f"|g[Complete] Agent #{aid} training finished! "
        f"Use 'agents' to see your roster and 'assign {aid}' "
        f"to put them to work.|n"
    )


def _fmt_agent_training_progress(d: dict) -> str:
    return f"|y[Training] Agent #{d['agent_id']}... {d['remaining']}s remaining|n"


def _fmt_harvest_drop(d: dict) -> str:
    return (
        f"|y[Harvest] +{d['amount']} {d['resource_type']} dropped. "
        f"Use 'get' to pick up.|n"
    )


def _fmt_attacked(d: dict) -> str:
    return (
        f"You were attacked by {d['attacker_name']} with {d['weapon_name']} "
        f"for {d['damage']} damage."
    )


def _fmt_building_attacked(d: dict) -> str:
    return (
        f"Your {d['building_name']} was attacked by {d['attacker_name']} "
        f"with {d['weapon_name']} for {d['damage']} damage."
    )


def _fmt_ability_active(d: dict) -> str:
    return f"|g[Ability] '{d['key']}' is now active for Agent #{d['agent_id']}.|n"


def _fmt_ability_relocked(d: dict) -> str:
    return (
        f"|r[Ability] '{d['key']}' has re-locked for Agent #{d['agent_id']} — "
        f"its level dropped below {d['required']}.|n"
    )


def _fmt_ability_available(d: dict) -> str:
    aid = d["agent_id"]
    return (
        f"|y[Ability] '{d['key']}' is now available for Agent #{aid}. "
        f"Enable it with 'agent ability {aid} {d['key']} on'.|n"
    )


class NotificationPresenter:
    """Formats ``PLAYER_NOTIFICATION`` events and delivers them to players."""

    #: kind -> (data dict) -> formatted string. The single source of truth for
    #: every per-player notification line.
    _FORMATTERS: dict[str, Callable[[dict], str]] = {
        "rank_level_up": _fmt_rank_level_up,
        "building_progress": _fmt_building_progress,
        "building_complete": _fmt_building_complete,
        "agent_training_complete": _fmt_agent_training_complete,
        "agent_training_progress": _fmt_agent_training_progress,
        "harvest_drop": _fmt_harvest_drop,
        "attacked": _fmt_attacked,
        "building_attacked": _fmt_building_attacked,
        "ability_active": _fmt_ability_active,
        "ability_relocked": _fmt_ability_relocked,
        "ability_available": _fmt_ability_available,
    }

    def __init__(self, event_bus: EventBus, player_notifier: Any = None) -> None:
        self.event_bus = event_bus
        from world.adapters.evennia_player_notifier import EvenniaPlayerNotifier

        self._notifier = player_notifier or EvenniaPlayerNotifier()
        event_bus.subscribe(PLAYER_NOTIFICATION, self.on_notification)

    def on_notification(
        self,
        event_name: str = "",
        player: Any = None,
        kind: str = "",
        data: dict | None = None,
        **kwargs,
    ) -> None:
        """Format the notification for its *kind* and deliver it to *player*."""
        formatter = self._FORMATTERS.get(kind)
        if formatter is None:
            logger.warning("No formatter for notification kind %r", kind)
            return
        try:
            message = formatter(data or {})
        except Exception:
            logger.exception("Failed to format notification kind %r: %r", kind, data)
            return
        self._notifier.notify(player, message)
