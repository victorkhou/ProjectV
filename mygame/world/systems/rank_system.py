"""
Rank System for the RTS Combat Overworld game.

Level-based progression with cosmetic ranks.

Players have a **level** (1-60).  Rank is derived: every 5 levels
advances the rank.  Levels 1-5 = Recruit, 6-10 = Private, …,
56-60 = Marshal.

All feature gates (buildings, planets, agent caps) use the player's
**level** directly.  Rank is a cosmetic title.

XP thresholds are defined per-level.  The YAML ``ranks.yaml`` defines
12 ranks with ``xp_threshold`` for the *first* level of each rank.
The 5 levels within a rank are linearly interpolated between
consecutive rank thresholds.

Requirements: 7.1-7.10, 4.2-4.9, 4b.1-4b.7
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from world.event_bus import RANK_PROMOTED, RANK_DEMOTED
from world.constants import (
    MAX_LEVEL,
    LEVELS_PER_RANK,
    NUM_RANKS,
    FINAL_RANK_XP_PER_LEVEL,
)

if TYPE_CHECKING:
    from world.data_registry import DataRegistry
    from world.definitions import RankDef
    from world.event_bus import EventBus

logger = logging.getLogger("mygame.rank_system")


def rank_from_level(level: int) -> int:
    """Derive rank number (1-NUM_RANKS) from player level (1-MAX_LEVEL)."""
    return min(NUM_RANKS, max(1, (level - 1) // LEVELS_PER_RANK + 1))


def level_range_for_rank(rank: int) -> tuple[int, int]:
    """Return (min_level, max_level) for a rank number (1-12)."""
    low = (rank - 1) * LEVELS_PER_RANK + 1
    high = rank * LEVELS_PER_RANK
    return low, min(high, MAX_LEVEL)


class RankSystem:
    """Manages player level/rank progression based on Combat XP.

    The player's ``db.level`` (1-60) is the authoritative progression
    value.  ``db.rank_level`` is kept in sync as ``rank_from_level(level)``
    for backward compatibility and display.

    Promotion/demotion events fire when the *rank* changes (every 5 levels).
    """

    def __init__(self, registry: "DataRegistry", event_bus: "EventBus",
                 planet_registry=None) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.planet_registry = planet_registry
        # Pre-compute XP thresholds for all 60 levels
        self._level_thresholds: list[int] = []
        self._rebuild_thresholds()

    def _rebuild_thresholds(self) -> None:
        """Build the XP threshold table for levels 1-60.

        Uses the rank xp_thresholds from the registry and linearly
        interpolates 5 levels between consecutive rank thresholds.
        """
        ranks = sorted(self.registry.ranks, key=lambda r: r.level)
        thresholds = [0] * (MAX_LEVEL + 1)  # index 0 unused, 1-60

        for i, rank_def in enumerate(ranks):
            base_xp = rank_def.xp_threshold
            if i + 1 < len(ranks):
                next_xp = ranks[i + 1].xp_threshold
            else:
                # Final rank: use a fixed interval per level
                next_xp = base_xp + LEVELS_PER_RANK * FINAL_RANK_XP_PER_LEVEL

            interval = (next_xp - base_xp) / LEVELS_PER_RANK
            for sub in range(LEVELS_PER_RANK):
                lvl = (rank_def.level - 1) * LEVELS_PER_RANK + sub + 1
                if 1 <= lvl <= MAX_LEVEL:
                    thresholds[lvl] = int(base_xp + sub * interval)

        self._level_thresholds = thresholds

    # ------------------------------------------------------------------ #
    #  XP threshold queries
    # ------------------------------------------------------------------ #

    def xp_for_level(self, level: int) -> int:
        """Return the XP threshold to reach *level*."""
        level = max(1, min(level, MAX_LEVEL))
        return self._level_thresholds[level]

    def level_for_xp(self, xp: int) -> int:
        """Return the highest level whose threshold is <= *xp*."""
        best = 1
        for lvl in range(1, MAX_LEVEL + 1):
            if self._level_thresholds[lvl] <= xp:
                best = lvl
            else:
                break
        return best

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def award_xp(self, player: Any, amount: int, reason: str = "") -> None:
        """Award Combat XP and check for level-up / promotion."""
        if amount <= 0:
            return
        old_level = self._get_level(player)
        player.db.combat_xp = (player.db.combat_xp or 0) + amount
        logger.info(
            "Awarded %d XP to %s (reason: %s). Total: %d",
            amount, getattr(player, "key", "?"), reason, player.db.combat_xp,
        )
        self._sync_level(player, old_level)

    def deduct_xp(self, player: Any, amount: int) -> None:
        """Deduct Combat XP (floor at 0) and check for level-down / demotion."""
        if amount <= 0:
            return
        old_level = self._get_level(player)
        player.db.combat_xp = max(0, (player.db.combat_xp or 0) - amount)
        logger.info(
            "Deducted %d XP from %s. Total: %d",
            amount, getattr(player, "key", "?"), player.db.combat_xp,
        )
        self._sync_level(player, old_level)

    def check_promotion(self, player: Any) -> None:
        """Re-sync level from XP (called externally if XP changed directly)."""
        old_level = self._get_level(player)
        self._sync_level(player, old_level)

    def check_demotion(self, player: Any) -> None:
        """Re-sync level from XP (called externally if XP changed directly)."""
        old_level = self._get_level(player)
        self._sync_level(player, old_level)

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def get_rank(self, player: Any) -> "RankDef":
        """Return the RankDef for the player's current rank."""
        level = self._get_level(player)
        rank_num = rank_from_level(level)
        rank_def = self._get_rank_by_level(rank_num)
        if rank_def is None:
            return self.registry.get_rank_for_xp(player.db.combat_xp or 0)
        return rank_def

    def get_rank_name(self, player: Any) -> str:
        """Return the cosmetic rank name for the player."""
        return self.get_rank(player).name.replace("_", " ")

    def get_status(self, player: Any) -> dict:
        """Return a dict with level/rank status info for display."""
        level = self._get_level(player)
        rank_num = rank_from_level(level)
        rank_def = self._get_rank_by_level(rank_num)
        rank_name = rank_def.name if rank_def else f"Rank {rank_num}"
        current_xp = player.db.combat_xp or 0

        # XP to next level
        xp_to_next_level = None
        if level < MAX_LEVEL:
            next_threshold = self.xp_for_level(level + 1)
            xp_to_next_level = next_threshold - current_xp

        # XP to next rank
        xp_to_next_rank = None
        next_rank = self._get_next_rank(rank_num)
        if next_rank is not None:
            xp_to_next_rank = next_rank.xp_threshold - current_xp

        # Sub-level within rank (1-5)
        _, _ = level_range_for_rank(rank_num)
        sub_level = ((level - 1) % LEVELS_PER_RANK) + 1

        return {
            "level": level,
            "rank_name": rank_name,
            "rank_level": rank_num,
            "sub_level": sub_level,
            "combat_xp": current_xp,
            "xp_to_next_level": xp_to_next_level,
            "xp_to_next_rank": xp_to_next_rank,
            "next_rank_name": next_rank.name if next_rank else None,
        }

    def get_sub_level(self, player: Any) -> int:
        """Return the sub-level (1-5) within the current rank."""
        level = self._get_level(player)
        return ((level - 1) % LEVELS_PER_RANK) + 1

    def can_access_planet(self, player: Any, planet_key: str) -> bool:
        """Check if a player's level allows access to a planet.

        Compares player level against the planet's rank_requirement
        (which is now a level requirement).
        """
        if self.planet_registry is None:
            return True
        try:
            space = self.planet_registry.get_space(planet_key)
        except KeyError:
            return False
        return self._get_level(player) >= space.rank_requirement

    # ------------------------------------------------------------------ #
    #  Agent cap
    # ------------------------------------------------------------------ #

    def get_agent_cap(self, player: Any) -> int:
        """Return the agent cap for the player's current rank."""
        return self.get_rank(player).agent_cap

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_level(player: Any) -> int:
        """Read the player's level, falling back to rank_level for compat.

        Old players only have rank_level (1-12, a rank number). Convert
        to the first level of that rank: ``(rank - 1) * 5 + 1``.
        """
        if hasattr(player, "db"):
            lvl = getattr(player.db, "level", None)
            if lvl is not None:
                return int(lvl)
            # Backward compat: old players only have rank_level (1-12)
            rl = getattr(player.db, "rank_level", None)
            if rl is not None:
                rl = int(rl)
                if rl <= NUM_RANKS:
                    # Old rank number → first level of that rank
                    return (rl - 1) * LEVELS_PER_RANK + 1
                return rl  # Already a level if > 12
        return 1

    def _sync_level(self, player: Any, old_level: int) -> None:
        """Recompute level from XP and fire events if rank changed."""
        xp = player.db.combat_xp or 0
        new_level = self.level_for_xp(xp)
        new_level = max(1, min(new_level, MAX_LEVEL))

        old_rank_num = rank_from_level(old_level)
        new_rank_num = rank_from_level(new_level)

        # Update stored level and rank_level
        player.db.level = new_level
        player.db.rank_level = new_rank_num

        # Notify on level change
        if new_level != old_level and hasattr(player, "msg"):
            rank_def = self._get_rank_by_level(new_rank_num)
            rank_name = rank_def.name.replace("_", " ") if rank_def else f"Rank {new_rank_num}"
            sub = ((new_level - 1) % LEVELS_PER_RANK) + 1
            player.msg(f"You are now Level {new_level} ({rank_name} {sub})")

        # Fire rank events if rank boundary crossed
        if new_rank_num > old_rank_num:
            old_rank_def = self._get_rank_by_level(old_rank_num)
            new_rank_def = self._get_rank_by_level(new_rank_num)
            if new_rank_def:
                self._unlock_for_rank(player, new_rank_num)
                logger.info(
                    "Promoted %s from %s to %s (level %d→%d)",
                    getattr(player, "key", "?"),
                    old_rank_def.name if old_rank_def else f"rank {old_rank_num}",
                    new_rank_def.name, old_level, new_level,
                )
                self.event_bus.publish(
                    RANK_PROMOTED,
                    player=player,
                    old_rank=old_rank_def,
                    new_rank=new_rank_def,
                    new_agent_cap=new_rank_def.agent_cap,
                )

        elif new_rank_num < old_rank_num:
            old_rank_def = self._get_rank_by_level(old_rank_num)
            new_rank_def = self._get_rank_by_level(new_rank_num)
            if new_rank_def:
                self._revoke_above_rank(player, new_rank_num)
                logger.info(
                    "Demoted %s from %s to %s (level %d→%d)",
                    getattr(player, "key", "?"),
                    old_rank_def.name if old_rank_def else f"rank {old_rank_num}",
                    new_rank_def.name, old_level, new_level,
                )
                self.event_bus.publish(
                    RANK_DEMOTED,
                    player=player,
                    old_rank=old_rank_def,
                    new_rank=new_rank_def,
                    new_agent_cap=new_rank_def.agent_cap,
                )

    def _get_rank_by_level(self, rank_num: int) -> "RankDef | None":
        """Find a RankDef by its rank number (1-12)."""
        for rank in self.registry.ranks:
            if rank.level == rank_num:
                return rank
        return None

    def _get_next_rank(self, current_rank_num: int) -> "RankDef | None":
        """Return the next rank above current_rank_num, or None."""
        for rank in self.registry.ranks:
            if rank.level == current_rank_num + 1:
                return rank
        return None

    def _unlock_for_rank(self, player: Any, rank_num: int) -> None:
        """Unlock techs/powerups available at rank_num and below."""
        techs = self.registry.get_technologies_for_rank(rank_num)
        researched = self._get_researched_techs(player)
        for tech in techs:
            researched.add(tech.key)
        self._set_researched_techs(player, researched)

    def _revoke_above_rank(self, player: Any, new_rank_num: int) -> None:
        """Revoke techs requiring ranks above new_rank_num."""
        available_techs = self.registry.get_technologies_for_rank(new_rank_num)
        available_keys = {t.key for t in available_techs}
        researched = self._get_researched_techs(player)
        researched = researched & available_keys
        self._set_researched_techs(player, researched)

    @staticmethod
    def _get_researched_techs(player: Any) -> set:
        techs = getattr(getattr(player, "db", None), "researched_techs", None)
        return set(techs) if techs else set()

    @staticmethod
    def _set_researched_techs(player: Any, techs: set) -> None:
        player.db.researched_techs = techs
