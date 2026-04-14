"""
Rank System for the RTS Combat Overworld game.

Manages player rank progression based on Combat XP. Handles promotion
when XP meets or exceeds the next rank's threshold, and demotion when
XP falls below the current rank's threshold. Unlocks/revokes
technologies and powerups on rank changes.

Also provides:
- Sub-level computation (5 levels per rank) for granular progress feedback
- Planet access gating based on rank vs planet rank_requirement
- Agent cap integration via RANK_PROMOTED / RANK_DEMOTED events

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10,
              4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9,
              4b.1, 4b.2, 4b.3, 4b.4, 4b.5, 4b.6, 4b.7
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from world.event_bus import RANK_PROMOTED, RANK_DEMOTED

if TYPE_CHECKING:
    from world.data_registry import DataRegistry
    from world.definitions import RankDef
    from world.event_bus import EventBus

logger = logging.getLogger("mygame.rank_system")


class RankSystem:
    """Manages player rank progression based on Combat XP.

    Promotion: After XP award, if combat_xp >= next_rank.xp_threshold,
    promote (set rank_level to new rank's level). Publish rank_promoted.

    Demotion: After XP deduction, if combat_xp < current_rank.xp_threshold,
    find correct lower rank and demote. Publish rank_demoted.
    """

    def __init__(self, registry: DataRegistry, event_bus: EventBus,
                 planet_registry=None) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.planet_registry = planet_registry

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def award_xp(self, player, amount: int, reason: str = "") -> None:
        """Award Combat XP to a player and check for promotion.

        Args:
            player: CombatCharacter (or fake) with db.combat_xp, db.rank_level.
            amount: Positive integer of XP to award.
            reason: Optional description of why XP was awarded.
        """
        if amount <= 0:
            return
        old_sub_level = self.get_sub_level(player)
        player.db.combat_xp = player.db.combat_xp + amount
        logger.info(
            "Awarded %d XP to %s (reason: %s). Total: %d",
            amount, getattr(player, "key", "?"), reason, player.db.combat_xp,
        )
        self.check_promotion(player)
        self._notify_sub_level_change(player, old_sub_level)

    def deduct_xp(self, player, amount: int) -> None:
        """Deduct Combat XP from a player and check for demotion.

        XP cannot go below 0.

        Args:
            player: CombatCharacter (or fake) with db.combat_xp, db.rank_level.
            amount: Positive integer of XP to deduct.
        """
        if amount <= 0:
            return
        old_sub_level = self.get_sub_level(player)
        player.db.combat_xp = max(0, player.db.combat_xp - amount)
        logger.info(
            "Deducted %d XP from %s. Total: %d",
            amount, getattr(player, "key", "?"), player.db.combat_xp,
        )
        self.check_demotion(player)
        self._notify_sub_level_change(player, old_sub_level)

    def check_promotion(self, player) -> None:
        """Promote the player if their XP qualifies for a higher rank.

        Handles multi-rank jumps (e.g. gaining enough XP to skip ranks).
        """
        ranks = self.registry.ranks  # sorted by level ascending
        if not ranks:
            return

        current_level = player.db.rank_level
        current_xp = player.db.combat_xp

        # Find the correct rank for the current XP
        new_rank = self.registry.get_rank_for_xp(current_xp)

        if new_rank.level > current_level:
            old_rank = self._get_rank_by_level(current_level)
            player.db.rank_level = new_rank.level

            # Unlock techs/powerups for the new rank and all below
            self._unlock_for_rank(player, new_rank.level)

            logger.info(
                "Promoted %s from %s to %s",
                getattr(player, "key", "?"),
                old_rank.name if old_rank else f"level {current_level}",
                new_rank.name,
            )

            self.event_bus.publish(
                RANK_PROMOTED,
                player=player,
                old_rank=old_rank,
                new_rank=new_rank,
                new_agent_cap=new_rank.agent_cap,
            )

    def check_demotion(self, player) -> None:
        """Demote the player if their XP no longer qualifies for current rank."""
        ranks = self.registry.ranks
        if not ranks:
            return

        current_level = player.db.rank_level
        current_xp = player.db.combat_xp

        current_rank = self._get_rank_by_level(current_level)
        if current_rank is None:
            return

        # If XP is still at or above current rank threshold, no demotion
        if current_xp >= current_rank.xp_threshold:
            return

        # Find the correct lower rank for the current XP
        new_rank = self.registry.get_rank_for_xp(current_xp)

        if new_rank.level < current_level:
            player.db.rank_level = new_rank.level

            # Revoke techs/powerups that required the lost rank(s)
            self._revoke_above_rank(player, new_rank.level)

            logger.info(
                "Demoted %s from %s to %s",
                getattr(player, "key", "?"),
                current_rank.name,
                new_rank.name,
            )

            self.event_bus.publish(
                RANK_DEMOTED,
                player=player,
                old_rank=current_rank,
                new_rank=new_rank,
                new_agent_cap=new_rank.agent_cap,
            )

    def get_rank(self, player) -> RankDef:
        """Return the RankDef for the player's current rank level."""
        rank = self._get_rank_by_level(player.db.rank_level)
        if rank is None:
            # Fallback: derive from XP
            return self.registry.get_rank_for_xp(player.db.combat_xp)
        return rank

    def get_status(self, player) -> dict:
        """Return a dict with rank status info for display.

        Requirement 7.10: current rank, XP, XP to next rank.
        Requirement 4b.5: includes sub-level and XP progress toward next level.
        """
        current_rank = self.get_rank(player)
        current_xp = player.db.combat_xp
        next_rank = self._get_next_rank(current_rank.level)
        sub_level = self.get_sub_level(player)

        xp_to_next = None
        if next_rank is not None:
            xp_to_next = next_rank.xp_threshold - current_xp

        # Compute XP to next sub-level
        xp_to_next_level = None
        if next_rank is not None:
            interval = (next_rank.xp_threshold - current_rank.xp_threshold) / 5
        else:
            # Final rank: fixed 10000 XP per level
            interval = 10000
        if sub_level < 5:
            next_level_xp = current_rank.xp_threshold + sub_level * interval
            xp_to_next_level = int(next_level_xp) - current_xp
        elif next_rank is not None:
            xp_to_next_level = next_rank.xp_threshold - current_xp

        return {
            "rank_name": current_rank.name,
            "rank_level": current_rank.level,
            "combat_xp": current_xp,
            "xp_to_next_rank": xp_to_next,
            "next_rank_name": next_rank.name if next_rank else None,
            "sub_level": sub_level,
            "xp_to_next_level": xp_to_next_level,
        }

    def get_sub_level(self, player) -> int:
        """Compute the player's sub-level (1-5) within their current rank.

        Each rank has 5 sub-levels with evenly spaced XP intervals.
        Level N starts at T1 + (N-1) × (T2-T1)/5 where T1 and T2 are
        consecutive rank thresholds.

        For the final rank (Marshal at 120000 XP), use a fixed interval
        of 10000 XP per level.

        Requirements: 4b.1, 4b.2, 4b.7
        """
        current_rank = self.get_rank(player)
        current_xp = player.db.combat_xp
        next_rank = self._get_next_rank(current_rank.level)

        t1 = current_rank.xp_threshold

        if next_rank is not None:
            interval = (next_rank.xp_threshold - t1) / 5
        else:
            # Final rank: fixed 10000 XP per level
            interval = 10000

        if interval <= 0:
            return 1

        # How far into this rank's XP range are we?
        xp_into_rank = current_xp - t1

        # Sub-level is 1-based: level 1 starts at 0 into rank,
        # level 2 at interval, level 3 at 2*interval, etc.
        level = int(xp_into_rank // interval) + 1

        # Cap at 5
        return min(level, 5)

    def can_access_planet(self, player, planet_key: str) -> bool:
        """Check if a player's rank allows access to a planet.

        Looks up the planet's rank_requirement from the PlanetRegistry
        and compares against the player's current rank level.

        Requirements: 1.4, 4.7
        """
        if self.planet_registry is None:
            # No planet registry available — allow by default
            return True

        try:
            space = self.planet_registry.get_space(planet_key)
        except KeyError:
            # Unknown planet — deny access
            return False

        return player.db.rank_level >= space.rank_requirement

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    def _notify_sub_level_change(self, player, old_sub_level: int) -> None:
        """Send a sub-level notification if the level changed.

        Requirement 4b.3, 4b.4: notify player on level change with
        "You are now {Rank Title} Level {N}."
        """
        new_sub_level = self.get_sub_level(player)
        if new_sub_level != old_sub_level:
            current_rank = self.get_rank(player)
            rank_name = current_rank.name.replace("_", " ")
            msg = f"You are now {rank_name} Level {new_sub_level}"
            if hasattr(player, "msg"):
                player.msg(msg)

    def _get_rank_by_level(self, level: int) -> RankDef | None:
        """Find a RankDef by its level number."""
        for rank in self.registry.ranks:
            if rank.level == level:
                return rank
        return None

    def _get_next_rank(self, current_level: int) -> RankDef | None:
        """Return the next rank above current_level, or None if at max."""
        for rank in self.registry.ranks:
            if rank.level == current_level + 1:
                return rank
        return None

    def _unlock_for_rank(self, player, rank_level: int) -> None:
        """Unlock all techs/powerups available at rank_level and below."""
        techs = self.registry.get_technologies_for_rank(rank_level)
        powerups = self.registry.get_powerups_for_rank(rank_level)

        researched = self._get_researched_techs(player)
        for tech in techs:
            researched.add(tech.key)
        self._set_researched_techs(player, researched)

        # Powerups are unlocked by being available — no persistent set
        # needed beyond rank gating. The PowerupSystem checks rank at
        # activation time. But we can store unlocked powerup keys if
        # the design requires it. For now, rank gating is sufficient.

    def _revoke_above_rank(self, player, new_rank_level: int) -> None:
        """Revoke techs/powerups that require ranks above new_rank_level."""
        # Get techs that ARE available at the new rank
        available_techs = self.registry.get_technologies_for_rank(new_rank_level)
        available_keys = {t.key for t in available_techs}

        researched = self._get_researched_techs(player)
        # Keep only techs that are still available at the new rank
        researched = researched & available_keys
        self._set_researched_techs(player, researched)

    def _get_researched_techs(self, player) -> set:
        """Get the player's researched_techs set."""
        techs = player.db.researched_techs
        if techs is None:
            return set()
        return set(techs)

    def _set_researched_techs(self, player, techs: set) -> None:
        """Set the player's researched_techs."""
        player.db.researched_techs = techs
