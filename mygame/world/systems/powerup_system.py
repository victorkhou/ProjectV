"""
Powerup System for the RTS Combat Overworld game.

Manages temporary combat buffs: activation with rank/cooldown checks,
tick-based duration tracking, and stat modifier queries.

"""

from __future__ import annotations

import logging
from typing import Any

from world.data_registry import DataRegistry
from world.event_bus import POWERUP_ACTIVATED, POWERUP_EXPIRED, EventBus
from world.systems.base_system import BaseSystem

logger = logging.getLogger("mygame.powerup_system")


class PowerupSystem(BaseSystem):
    """Manages powerup activation, duration, and cooldown.

    Active powerups are stored on the player as ``player.db.active_powerups``
    (dict of key -> {expires_tick, effect}). Cooldowns are stored as
    ``player.db.powerup_cooldowns`` (dict of key -> ready_tick).

    Args:
        registry: The DataRegistry holding powerup/rank definitions.
        event_bus: The EventBus for publishing game events.
        current_tick_func: Callable returning the current game tick.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        current_tick_func=None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._current_tick_func = current_tick_func or (lambda: 0)
        # Track players with active powerups for process_tick
        self._active_players: set = set()

    # ------------------------------------------------------------------ #
    #  Activation
    # ------------------------------------------------------------------ #

    def activate(
        self, player: Any, powerup_key: str
    ) -> tuple[bool, str]:
        """Activate a powerup on a player.

        Validation:
            1. Powerup key exists in registry
            2. Player rank meets required_rank
            3. Powerup is not on cooldown
            4. Powerup is not already active

        On success:
            - Store in player.db.active_powerups
            - Set cooldown in player.db.powerup_cooldowns
            - Publish powerup_activated event

        Returns:
            (success, message) tuple.
        """
        # 1. Look up powerup definition
        pdef = self.registry.powerups.get(powerup_key)
        if pdef is None:
            return False, f"Unknown powerup: {powerup_key}"

        # 2. Rank check — compare player's derived rank against required rank
        player_level = self._get_player_level(player)
        try:
            required_rank = self.registry.get_rank_by_name(pdef.required_rank)
            from world.systems.rank_system import rank_from_level
            player_rank = rank_from_level(player_level)
            if player_rank < required_rank.level:
                return False, (
                    f"Requires rank {pdef.required_rank} "
                    f"(you are level {player_level})."
                )
        except (KeyError, ImportError):
            pass  # If rank not found, allow activation

        current_tick = self._current_tick_func()

        # 3. Cooldown check
        cooldowns = self._get_cooldowns(player)
        ready_tick = cooldowns.get(powerup_key, 0)
        if ready_tick > current_tick:
            remaining = ready_tick - current_tick
            return False, (
                f"Powerup on cooldown: {remaining} ticks remaining."
            )

        # 4. Already active check
        active = self._get_active_powerups(player)
        if powerup_key in active:
            return False, f"Powerup {powerup_key} is already active."

        # Apply: store active powerup
        expires_tick = current_tick + pdef.duration_ticks
        active[powerup_key] = {
            "expires_tick": expires_tick,
            "effect": {
                "effect_type": pdef.effect_type,
                "effect_value": pdef.effect_value,
            },
        }
        self._set_active_powerups(player, active)

        # Set cooldown
        cooldowns[powerup_key] = current_tick + pdef.cooldown_ticks
        self._set_cooldowns(player, cooldowns)

        # Track player for process_tick
        self._active_players.add(player)

        # Publish event
        self.event_bus.publish(
            POWERUP_ACTIVATED,
            player=player,
            powerup=pdef,
        )

        logger.info(
            "Activated powerup %s on %s (expires tick %d)",
            powerup_key, getattr(player, "key", "?"), expires_tick,
        )

        return True, f"Activated {pdef.name} for {pdef.duration_ticks} ticks."

    # ------------------------------------------------------------------ #
    #  Tick processing
    # ------------------------------------------------------------------ #

    def process_tick(self, current_tick: int) -> None:
        """Decrement durations and remove expired powerups.

        Iterates over all tracked players with active powerups,
        removes expired entries, and publishes powerup_expired events.

        Args:
            current_tick: The current game tick number.
        """
        players_to_remove = set()

        for player in list(self._active_players):
            active = self._get_active_powerups(player)
            if not active:
                players_to_remove.add(player)
                continue

            expired_keys = []
            for key, data in list(active.items()):
                if data["expires_tick"] <= current_tick:
                    expired_keys.append(key)

            for key in expired_keys:
                del active[key]

                # Publish powerup_expired event
                pdef = self.registry.powerups.get(key)
                self.event_bus.publish(
                    POWERUP_EXPIRED,
                    player=player,
                    powerup=pdef,
                )
                logger.info(
                    "Powerup %s expired on %s at tick %d",
                    key, getattr(player, "key", "?"), current_tick,
                )

            self._set_active_powerups(player, active)

            if not active:
                players_to_remove.add(player)

        self._active_players -= players_to_remove

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def get_active_powerups(self, player: Any) -> list[dict]:
        """Return a list of active powerup info dicts for the player.

        Each dict contains: key, expires_tick, effect_type, effect_value.
        """
        active = self._get_active_powerups(player)
        result = []
        for key, data in active.items():
            effect = data.get("effect", {})
            result.append({
                "key": key,
                "expires_tick": data["expires_tick"],
                "effect_type": effect.get("effect_type", ""),
                "effect_value": effect.get("effect_value", 0),
            })
        return result

    def get_stat_modifier(self, player: Any, stat: str) -> float:
        """Return the total stat modifier from active powerups.

        Sums effect_value for all active powerups whose effect_type
        matches the given stat name.

        Args:
            player: The player to query.
            stat: The stat name to look up (e.g. "damage_bonus").

        Returns:
            Total modifier value (0.0 if no matching powerups).
        """
        active = self._get_active_powerups(player)
        total = 0.0
        for _key, data in active.items():
            effect = data.get("effect", {})
            if effect.get("effect_type") == stat:
                total += float(effect.get("effect_value", 0))
        return total

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_player_level(player: Any) -> int:
        """Read the player's level (1-60). See ``world.utils.get_player_level``."""
        from world.utils import get_player_level
        return get_player_level(player, default=0)

    @staticmethod
    def _get_active_powerups(player: Any) -> dict:
        """Read active_powerups dict from the player."""
        if hasattr(player, "db"):
            ap = getattr(player.db, "active_powerups", None)
            if ap is None:
                ap = {}
                player.db.active_powerups = ap
            return ap
        return {}

    @staticmethod
    def _set_active_powerups(player: Any, active: dict) -> None:
        """Write active_powerups dict to the player."""
        if hasattr(player, "db"):
            player.db.active_powerups = active

    @staticmethod
    def _get_cooldowns(player: Any) -> dict:
        """Read powerup_cooldowns dict from the player."""
        if hasattr(player, "db"):
            cd = getattr(player.db, "powerup_cooldowns", None)
            if cd is None:
                cd = {}
                player.db.powerup_cooldowns = cd
            return cd
        return {}

    @staticmethod
    def _set_cooldowns(player: Any, cooldowns: dict) -> None:
        """Write powerup_cooldowns dict to the player."""
        if hasattr(player, "db"):
            player.db.powerup_cooldowns = cooldowns
