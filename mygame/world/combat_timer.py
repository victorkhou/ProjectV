"""
Combat timer subscriber — starts/resets the 60-second combat timer on players.

When a COMBAT_ACTION event fires (with ``attacker``/``target`` kwargs
from the CombatEngine, or a direct ``player`` kwarg from vision events),
sets ``player.db.combat_timer_expires = current_tick + 60`` on every
involved player and publishes a COMBAT_TIMER_STARTED event for each.

The timer is tick-based: GameTickScript clears it when the current tick
reaches the expiry value. CmdMove blocks Wall passage while active.

"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from world.constants import COMBAT_TIMER_DURATION

if TYPE_CHECKING:
    from world.event_bus import EventBus

logger = logging.getLogger("mygame.combat_timer")


def _get_current_tick() -> int:
    """Return the current game tick from the GameTickScript."""
    try:
        from evennia.utils.search import search_script

        scripts = search_script("game_tick")
        if scripts:
            return getattr(scripts[0].db, "tick_count", 0) or 0
    except Exception:
        pass
    return 0


def _is_player(entity: Any) -> bool:
    """Return True if *entity* looks like a player character."""
    from world.utils import is_player
    return is_player(entity)


def on_combat_action(event_bus: "EventBus", **kwargs) -> None:
    """Subscriber for COMBAT_ACTION events.

    The CombatEngine publishes with ``attacker=`` and ``target=``
    kwargs.  For every participant that is a player character, sets
    (or resets) ``combat_timer_expires = current_tick + 60``.

    Also accepts a direct ``player=`` kwarg for convenience (e.g.
    vision-triggered timers).
    """
    from world.event_bus import COMBAT_TIMER_STARTED, PLAYER_NOTIFICATION

    # Prefer the tick supplied by the publisher (CombatEngine passes its injected
    # clock in the event payload) so we avoid a search_script DB query on EVERY
    # hit — a real per-tick cost when many turret/guard/AoE hits land. Fall back
    # to the live lookup only for publishers that don't supply it (vision events).
    current_tick = kwargs.get("current_tick")
    if current_tick is None:
        current_tick = _get_current_tick()
    new_expiry = current_tick + COMBAT_TIMER_DURATION

    # Collect all player entities involved in this combat action
    players: list[Any] = []

    # Direct player kwarg (vision events, manual triggers)
    direct = kwargs.get("player")
    if direct is not None and _is_player(direct):
        players.append(direct)

    # Attacker / target from CombatEngine.resolve_tick, PLUS the owning players
    # behind them (attacker_owner / target_owner). A fight involving A's turret
    # or A's agent must put A into combat too — not only the units that traded
    # blows. The owner kwargs are pre-resolved by the engine; each is a player
    # (or None), so the same _is_player gate applies uniformly.
    for key in ("attacker", "target", "attacker_owner", "target_owner"):
        entity = kwargs.get(key)
        if entity is not None and _is_player(entity) and entity not in players:
            players.append(entity)

    for player in players:
        db = getattr(player, "db", None)
        if db is None:
            continue

        # Detect the OUT-OF-COMBAT -> IN-COMBAT transition so the player is told
        # once when a fight begins, not on every hit (each hit resets the
        # timer). "In combat" = an expiry strictly in the future.
        prev_expiry = getattr(db, "combat_timer_expires", 0) or 0
        was_in_combat = prev_expiry > current_tick

        db.combat_timer_expires = new_expiry

        if not was_in_combat:
            try:
                event_bus.publish(
                    PLAYER_NOTIFICATION, player=player,
                    kind="combat_started",
                    data={"duration": COMBAT_TIMER_DURATION},
                )
            except Exception:
                logger.exception("Failed to publish combat_started notification")

        try:
            event_bus.publish(
                COMBAT_TIMER_STARTED, player=player, expires=new_expiry
            )
        except Exception:
            logger.exception("Failed to publish COMBAT_TIMER_STARTED event")


def subscribe_combat_timer(event_bus: EventBus) -> None:
    """Wire the combat timer subscriber to the event bus.

    Called from game_init.py during server startup.
    """
    from world.event_bus import COMBAT_ACTION

    # Use a closure so the subscriber has a reference to event_bus
    # for publishing COMBAT_TIMER_STARTED.
    def _handler(**kwargs):
        on_combat_action(event_bus, **kwargs)

    event_bus.subscribe(COMBAT_ACTION, _handler)
    logger.info("Combat timer subscriber wired to COMBAT_ACTION events.")
