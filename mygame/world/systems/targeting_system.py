"""
Targeting system — ranged lock-on for the 'target'/'shoot' commands.

A player with a ranged weapon can ``target`` an enemy to lock on over a few
ticks; once locked, ``shoot`` hits at the higher targeted accuracy and tracks
the enemy as long as it stays in weapon range. A lock is a *held aim*: it breaks
the instant the SHOOTER moves (handled in ``CombatCharacter.at_coord_change``),
and the per-tick upkeep additionally drops it if the tracked enemy leaves weapon
range, the shooter changes planet, or the weapon is unequipped. The lock is
stored on the shooter's ``db`` and driven by a per-tick upkeep step:

- ``db.lock_target``   — the enemy being locked/tracked (object ref), or None.
- ``db.lock_progress`` — ticks of lock accumulated so far.
- ``db.lock_ready``    — True once progress reaches the (weapon-adjusted) lock
                         time; a ready lock grants ``accuracy_targeted``.

Framework-free (no Evennia imports): coordinates/owner/weapon are read via the
shared ``world.utils`` helpers and the equipment handler, and all player-facing
text is emitted as ``PLAYER_NOTIFICATION`` events for the presenter.
"""

from __future__ import annotations

from typing import Any

from world.data_registry import DataRegistry
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem


class TargetingSystem(BaseSystem):
    """Manage per-player ranged lock-on state (acquire, upkeep, queries).

    Args:
        registry: DataRegistry holding the balance config (lock time, accuracy).
        event_bus: EventBus for player notifications.
    """

    def __init__(self, registry: DataRegistry, event_bus: EventBus) -> None:
        super().__init__(registry, event_bus)

    # ------------------------------------------------------------------ #
    #  Weapon / accuracy helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_ranged_weapon(player: Any) -> Any | None:
        """Return *player*'s equipped RANGED weapon, or None.

        A weapon is ranged when its ``weapon_type`` is ``"ranged"``. Melee
        weapons (or an empty slot) yield None, so the 'target'/'shoot' commands
        can reject a player with no ranged weapon.
        """
        equipment = getattr(player, "equipment", None)
        if equipment is None or not hasattr(equipment, "get_equipped"):
            return None
        weapon = equipment.get_equipped("weapon")
        if weapon is None:
            return None
        wtype = getattr(weapon, "weapon_type", None)
        if wtype is None and hasattr(weapon, "attributes"):
            wtype = weapon.attributes.get("weapon_type", default=None)
        return weapon if wtype == "ranged" else None

    @staticmethod
    def weapon_range(weapon: Any) -> int:
        """Return a weapon's ``range`` stat (default 1)."""
        if weapon is not None and hasattr(weapon, "get_stat"):
            try:
                return int(weapon.get_stat("range", 1))
            except (TypeError, ValueError):
                return 1
        return 1

    @staticmethod
    def _weapon_stat(weapon: Any, stat: str, default: float = 0.0) -> float:
        if weapon is not None and hasattr(weapon, "get_stat"):
            try:
                return float(weapon.get_stat(stat, default))
            except (TypeError, ValueError):
                return default
        return default

    def lock_ticks_for(self, weapon: Any) -> int:
        """Ticks needed to fully lock, reduced by the weapon's ``lock_speed``.

        ``target_lock_ticks - lock_speed``, floored at 1 — a weapon with a
        ``lock_speed`` modifier locks faster, but a lock always takes at least
        one tick.
        """
        base = int(self.registry.balance.target_lock_ticks)
        speed = int(self._weapon_stat(weapon, "lock_speed", 0))
        return max(1, base - speed)

    def targeted_accuracy(self, weapon: Any) -> float:
        """Hit chance vs a locked target: baseline + weapon ``accuracy`` (0..1)."""
        base = float(self.registry.balance.accuracy_targeted)
        return _clamp01(base + self._weapon_stat(weapon, "accuracy", 0.0))

    def directional_accuracy(self, weapon: Any) -> float:
        """Hit chance for a directional shot: baseline + weapon ``accuracy``."""
        base = float(self.registry.balance.accuracy_directional)
        return _clamp01(base + self._weapon_stat(weapon, "accuracy", 0.0))

    # ------------------------------------------------------------------ #
    #  Lock queries / mutation
    # ------------------------------------------------------------------ #

    def in_weapon_range(self, player: Any, target: Any, weapon: Any) -> bool:
        """True if *target* is within *weapon*'s range of *player* (Chebyshev).

        The public range check used by ``shoot`` to re-validate a locked target
        at fire time (before the per-tick upkeep runs), so a shot at a target
        that just stepped out of range is refused with feedback rather than
        silently dropped by the engine after consuming ammo.
        """
        return self._in_range(player, target, self.weapon_range(weapon))

    @staticmethod
    def get_target(player: Any) -> Any | None:
        """Return the enemy *player* is locking/locked onto, or None."""
        db = getattr(player, "db", None)
        return getattr(db, "lock_target", None) if db is not None else None

    @staticmethod
    def is_locked(player: Any) -> bool:
        """True if *player* has a COMPLETED lock (ready to fire at high accuracy)."""
        db = getattr(player, "db", None)
        if db is None:
            return False
        return bool(getattr(db, "lock_ready", False)) and \
            getattr(db, "lock_target", None) is not None

    def clear_lock(self, player: Any, reason: str | None = None) -> None:
        """Drop *player*'s lock, optionally notifying them why it broke."""
        db = getattr(player, "db", None)
        if db is None:
            return
        had = getattr(db, "lock_target", None) is not None
        db.lock_target = None
        db.lock_progress = 0
        db.lock_ready = False
        if had and reason:
            self.notify(player, "lock_lost", reason=reason)

    def acquire(self, player: Any, target: Any) -> tuple[bool, str]:
        """Begin locking *player* onto *target*.

        Requires a ranged weapon and the target within its range on the same
        planet. Starts a fresh lock (progress 0); the per-tick upkeep advances
        it to ready. Re-targeting the SAME enemy is a no-op that keeps progress.

        Returns ``(ok, message)``; on success the message is empty (the
        ``targeting`` notification carries the player-facing text).
        """
        weapon = self.get_ranged_weapon(player)
        if weapon is None:
            return False, "You need a ranged weapon equipped to lock on."
        if target is None or target is player:
            return False, "No valid target."

        rng = self.weapon_range(weapon)
        if not self._in_range(player, target, rng):
            return False, "That target is out of your weapon's range."

        db = player.db
        if getattr(db, "lock_target", None) is target:
            # Already locking this one — don't reset progress.
            return True, ""
        db.lock_target = target
        db.lock_progress = 0
        db.lock_ready = False
        self.notify(player, "targeting",
                    target_name=getattr(target, "key", "the target"),
                    ticks=self.lock_ticks_for(weapon))
        return True, ""

    # ------------------------------------------------------------------ #
    #  Per-tick upkeep
    # ------------------------------------------------------------------ #

    def process_tick(self, tick_number: int, players: list) -> None:
        """Advance / validate every player's lock this tick.

        For each player with a lock: drop it if the weapon is gone, the shooter
        changed planet, or the target left weapon range; otherwise accumulate
        progress and flip ``lock_ready`` once it reaches the weapon-adjusted lock
        time (notifying ``locked`` on the transition). Each player is isolated so
        one bad entry never halts the step.
        """
        if not players:
            return
        for player in players:
            try:
                self._upkeep_one(player)
            except Exception:  # noqa: BLE001 - one bad lock must not halt the step
                from world.systems.agent_constants import logger
                logger.exception(
                    "Targeting upkeep error for %s", getattr(player, "key", "?")
                )

    def _upkeep_one(self, player: Any) -> None:
        db = getattr(player, "db", None)
        if db is None or getattr(db, "lock_target", None) is None:
            return
        target = db.lock_target

        # Target gone / dead → drop silently-ish (it's no longer a valid foe).
        if getattr(target, "pk", True) is None:
            self.clear_lock(player, reason="target_gone")
            return

        weapon = self.get_ranged_weapon(player)
        if weapon is None:
            self.clear_lock(player, reason="no_weapon")
            return

        # Same planet? (Changing rooms breaks the lock.)
        if self._planet(player) != self._planet(target):
            self.clear_lock(player, reason="left_area")
            return

        # Still in weapon range?
        if not self._in_range(player, target, self.weapon_range(weapon)):
            self.clear_lock(player, reason="out_of_range")
            return

        if getattr(db, "lock_ready", False):
            return  # already locked; nothing to advance

        progress = int(getattr(db, "lock_progress", 0) or 0) + 1
        db.lock_progress = progress
        if progress >= self.lock_ticks_for(weapon):
            db.lock_ready = True
            self.notify(player, "locked",
                        target_name=getattr(target, "key", "the target"))

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _planet(entity: Any) -> Any:
        db = getattr(entity, "db", None)
        return getattr(db, "coord_planet", None) if db is not None else None

    @staticmethod
    def _in_range(a: Any, b: Any, weapon_range: int) -> bool:
        from world.utils import chebyshev_distance, get_coords
        ca = get_coords(a)
        cb = get_coords(b)
        if ca is None or cb is None:
            return False
        return chebyshev_distance(ca[0], ca[1], cb[0], cb[1]) <= weapon_range


def _clamp01(value: float) -> float:
    """Clamp *value* to the closed interval [0, 1]."""
    return max(0.0, min(1.0, value))
