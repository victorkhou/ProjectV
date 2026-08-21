"""
Rank System for the RTS Combat Overworld game.

Level-based progression with cosmetic ranks.

Players have a **level** (1-100).  Rank is derived from the widening
``RANK_BANDS`` mapping in ``world/constants.py`` (Recruit L1-5,
Private L6-10, …, General L85-99), with Marshal as the L100 capstone.
Band widths vary by rank.

All feature gates (buildings, planets, agent caps) use the player's
**level** directly.  Rank is a cosmetic title.  Ranks never grant or
revoke technologies — techs are acquired only by research at a Lab.

XP thresholds come from the hybrid formula in ``world/progression.py``
(40 XP at L2, +20%/level to L20, +5%/level to L100).  The YAML
``ranks.yaml`` carries rank names, agent caps, and planet access; its
``xp_threshold`` values are legacy display data only.

"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from world import progression
from world.event_bus import RANK_PROMOTED, RANK_DEMOTED, LEVEL_CHANGED
from world.systems.base_system import BaseSystem
from world.constants import (
    MAX_LEVEL,
    NUM_RANKS,
    RANK_BANDS,
    XP_GAIN_SUPPRESSED_REASONS,
)

if TYPE_CHECKING:
    from world.data_registry import DataRegistry
    from world.definitions import RankDef
    from world.event_bus import EventBus

logger = logging.getLogger("mygame.rank_system")


def rank_from_level(level: int) -> int:
    """Derive rank number (1-NUM_RANKS) from player level (1-MAX_LEVEL).

    A ``RANK_BANDS`` lookup (R14.5 — widening bands replaced the uniform
    LEVELS_PER_RANK width). Levels below band 1 clamp to rank 1; levels above
    the last band clamp to NUM_RANKS.
    """
    level = max(1, min(int(level or 1), MAX_LEVEL))
    for rank, (low, high) in RANK_BANDS.items():
        if low <= level <= high:
            return rank
    return NUM_RANKS if level > RANK_BANDS[NUM_RANKS][1] else 1


def level_range_for_rank(rank: int) -> tuple[int, int]:
    """Return (min_level, max_level) for a rank number (1-12) per RANK_BANDS."""
    band = RANK_BANDS.get(int(rank))
    if band is None:
        return 1, MAX_LEVEL
    return band


def player_meets_rank(player_level: int, required_rank_name: str, registry) -> bool:
    """Return True if a player at *player_level* satisfies *required_rank_name*.

    The single rank-requirement gate shared by every content gate (equipment
    equip/use, bomb deploy, powerup activation, tech research): map the level to
    a rank number via :func:`rank_from_level` and compare against the required
    rank's ``.level``. FALLS OPEN — returns True — when *required_rank_name* is
    empty/unset or does not resolve to a loaded rank, so unknown or missing rank
    content never hard-blocks the action. Callers keep their own rejection
    side-effect (notification, message); this only decides the boolean.
    """
    if not required_rank_name:
        return True
    try:
        required = registry.get_rank_by_name(required_rank_name)
    except (KeyError, AttributeError):
        return True
    return rank_from_level(player_level) >= required.level


class RankSystem(BaseSystem):
    """Manages player level/rank progression based on Combat XP.

    The player's ``db.level`` (1-100) is the authoritative progression
    value.  ``db.rank_level`` is kept in sync as ``rank_from_level(level)``
    for backward compatibility and display.

    Promotion/demotion events fire when the *rank* changes (i.e. when the
    level crosses a ``RANK_BANDS`` boundary).
    """

    def __init__(self, registry: "DataRegistry", event_bus: "EventBus",
                 planet_registry=None) -> None:
        super().__init__(registry, event_bus)
        self.planet_registry = planet_registry
        # The level->XP curve lives in ``world.progression`` (the single
        # source of truth shared with ``CombatEntity``). Build the table
        # from this registry's ranks if it has not been initialized yet.
        if not progression.is_initialized():
            self._rebuild_thresholds()

    def _rebuild_thresholds(self) -> None:
        """(Re)build the shared ``world.progression`` threshold table.

        Thin wrapper over ``world.progression.build_thresholds``. The curve
        computation (the hybrid growth formula: 40 XP at L2, +20%/level to
        L20, +5%/level to L100) lives in the shared helper so
        ``CombatEntity`` and ``RankSystem`` derive levels from one place
        rather than duplicating it. Calling this rebuilds the table from
        this system's registry.
        """
        progression.build_thresholds(self.registry.ranks)

    # ------------------------------------------------------------------ #
    #  XP threshold queries
    # ------------------------------------------------------------------ #

    def xp_for_level(self, level: int) -> int:
        """Return the XP threshold to reach *level* (delegates to progression)."""
        return progression.xp_for_level(level)

    def level_for_xp(self, xp: int) -> int:
        """Return the highest level whose threshold is <= *xp* (delegates)."""
        return progression.level_for_xp(xp)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def award_xp(self, player: Any, amount: int, reason: str = "") -> int:
        """Award Combat XP and check for level-up / promotion.

        Delegates the XP mutation to the entity's ``CombatEntity.award_xp``
        method, then syncs player-facing level/rank state and fires events.

        The outgrown-planet throttle (§4) scales XP by the player's
        outgrown_factor for their current planet — a player who COULD graduate
        but is camping earns less XP, incentivizing progression up the ladder.

        Returns the amount ACTUALLY awarded (post-throttle), or 0 when nothing
        was awarded, so a caller that reports the gain to the player can quote
        the real figure rather than its pre-throttle request.
        """
        if amount <= 0:
            return 0
        # Apply the outgrown-planet XP throttle (§4).
        from world.utils import outgrown_factor
        factor = outgrown_factor(player)
        if factor < 1.0:
            amount = max(1, int(round(amount * factor)))
        old_level = self._get_level(player)
        player.award_xp(amount)
        logger.info(
            "Awarded %d XP to %s (reason: %s). Total: %d",
            amount, getattr(player, "key", "?"), reason, player.db.combat_xp,
        )
        # Surface the gain to the player (feel the bar fill), but only for the
        # sources that are otherwise SILENT. Reasons whose own notification
        # already quotes the XP are suppressed here (see
        # XP_GAIN_SUPPRESSED_REASONS) — a second line would double-report, and
        # would disagree whenever the outgrown throttle has scaled the award.
        # Fires before _sync_level so "+N XP" reads just above any "LEVEL UP!"
        # banner the sync then emits.
        if reason not in XP_GAIN_SUPPRESSED_REASONS:
            self.notify(player, "xp_gain", amount=amount, reason=reason or None)
        self._sync_level(player, old_level)
        return amount

    def deduct_xp(self, player: Any, amount: int) -> None:
        """Deduct Combat XP (floor at 0) and check for level-down / demotion.

        Delegates the XP mutation to the entity's ``CombatEntity.deduct_xp``
        method, then syncs player-facing level/rank state and fires events.
        """
        if amount <= 0:
            return
        old_level = self._get_level(player)
        player.deduct_xp(amount)
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
            # ranks.yaml lacks an entry for this rank number (data gap). Fall
            # back to the highest defined rank at or below it — NOT to an
            # xp_threshold ranking: ranks.yaml xp_thresholds are stale display
            # data under the R14 formula-derived curve and would disagree with
            # the band-derived rank.
            candidates = [r for r in self.registry.ranks if r.level <= rank_num]
            if candidates:
                return max(candidates, key=lambda r: r.level)
            return self.registry.ranks[0]
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
        # Next rank begins at the next band's start level (R14 — rank defs no
        # longer carry authoritative xp_thresholds; the hybrid curve does).
        xp_to_next_rank = None
        next_rank = self._get_next_rank(rank_num)
        if next_rank is not None:
            next_band_start, _ = level_range_for_rank(rank_num + 1)
            xp_to_next_rank = progression.xp_for_level(next_band_start) - current_xp

        # Sub-level within the rank's band (1..band_width)
        band_low, _ = level_range_for_rank(rank_num)
        sub_level = level - band_low + 1

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
        """Return the sub-level (1..band_width) within the current rank's band."""
        level = self._get_level(player)
        band_low, _ = level_range_for_rank(rank_from_level(level))
        return level - band_low + 1

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

    def _check_planet_unlocks(self, old_level: int, new_level: int) -> list[dict]:
        """Return planets unlocked between old_level (exclusive) and new_level.

        Each entry is ``{"name": "Forge", "type": "industrial", "key": "forge"}``.
        Only includes planets whose ``rank_requirement`` falls in the
        ``(old_level, new_level]`` range — i.e. planets the player just gained
        access to at this level-up.
        """
        if self.planet_registry is None:
            return []
        unlocked = []
        for key in self.planet_registry.list_planets():
            try:
                space = self.planet_registry.get_space(key)
            except KeyError:
                continue
            req = space.rank_requirement
            if old_level < req <= new_level:
                unlocked.append({
                    "name": key.capitalize(),
                    "type": getattr(space, "planet_type", "unknown"),
                    "key": key,
                })
        return unlocked

    #: Most buildings to name in a level-up unlock list before collapsing the
    #: rest into an "…and N more" tail. A big single award (a fortress kill can
    #: jump 13 levels) would otherwise bury the banner in unlock lines.
    _MAX_UNLOCKS_LISTED = 4

    def _check_building_unlocks(
        self, player: Any, old_level: int, new_level: int
    ) -> list[str]:
        """Return buildings *player* can ACTUALLY build now, newly opened by
        crossing into ``new_level``.

        A building's ``rank_requirement`` is a LEVEL gate, but it is only one of
        the gates ``BuildingSystem._validate_construction`` enforces — so a name
        is only listed here when the player also satisfies the other *durable*
        gates, otherwise the level-up would promise something the build command
        immediately refuses:

        * ``unlock_deed`` / ``unlock_deed_count`` — deed-gated buildings (the
          Barracks, the four labs, the Blacksmith, the Refinery) are omitted
          until the player holds the deeds.
        * ``requires_hq`` — a player with no HQ cannot build these at all, so
          they are omitted (the HQ directive is guiding them there anyway).

        Per-tile gates (terrain, per-planet caps) are deliberately NOT consulted:
        they depend on *where* the player builds, not on whether the building is
        available to them, and a tile-specific refusal is not a broken promise.

        Sorted by gate level then name for a stable read, and capped at
        :attr:`_MAX_UNLOCKS_LISTED` with an "…and N more" tail. Returns ``[]``
        when the registry is unavailable. Never raises — a malformed
        ``rank_requirement`` skips that entry rather than losing the level-up
        notification (this runs inside the XP-award path, where the XP has
        already been applied).
        """
        try:
            registry = getattr(self, "registry", None)
            buildings = getattr(registry, "buildings", None)
            if not buildings:
                return []

            deeds = getattr(getattr(player, "db", None), "deeds", None) or {}
            if not isinstance(deeds, dict):
                deeds = {}

            newly: list[tuple[int, str]] = []
            has_hq: bool | None = None  # resolved lazily, at most once
            for bdef in buildings.values():
                try:
                    req = int(getattr(bdef, "rank_requirement", 1) or 1)
                except (TypeError, ValueError):
                    continue  # malformed gate — skip this entry, not the banner
                if not (old_level < req <= new_level):
                    continue
                # Deed gate: omit what the player cannot yet build.
                deed = getattr(bdef, "unlock_deed", None)
                if deed:
                    try:
                        required = int(getattr(bdef, "unlock_deed_count", 1) or 1)
                    except (TypeError, ValueError):
                        required = 1
                    if int(deeds.get(deed, 0) or 0) < required:
                        continue
                # HQ gate: a player without an HQ cannot build these at all.
                if getattr(bdef, "requires_hq", False):
                    if has_hq is None:
                        has_hq = self._player_has_hq(player)
                    if not has_hq:
                        continue
                newly.append((req, str(getattr(bdef, "name", None) or "?")))

            if not newly:
                return []

            names = [name for _req, name in sorted(newly)]
            if len(names) > self._MAX_UNLOCKS_LISTED:
                extra = len(names) - self._MAX_UNLOCKS_LISTED
                names = names[: self._MAX_UNLOCKS_LISTED]
                names.append(f"…and {extra} more")
            return names
        except Exception:  # noqa: BLE001 - never lose the level-up notification
            logger.exception("Building-unlock check failed; omitting the list.")
            return []

    @staticmethod
    def _player_has_hq(player: Any) -> bool:
        """Return True if *player* owns a completed HQ on their current planet.

        Reuses the shared ``owner_has_active_hq`` predicate. Falls back to
        ``True`` when the query is unavailable (isolated tests with no building
        roster) so the unlock list is not suppressed purely by a missing fake.
        """
        try:
            from world.utils import owner_has_active_hq
            if not hasattr(player, "get_buildings"):
                return True  # cannot tell — do not suppress
            planet = getattr(getattr(player, "db", None), "coord_planet", None)
            return bool(owner_has_active_hq(player, planet))
        except Exception:  # noqa: BLE001
            return True

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
        to the first level of that rank: ``(rank - 1) * 5 + 1``. Delegates
        to ``world.utils.get_player_level`` (the single source of truth).
        """
        from world.utils import get_player_level
        return get_player_level(player, default=1)

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
        if new_level != old_level:
            rank_def = self._get_rank_by_level(new_rank_num)
            rank_name = rank_def.name.replace("_", " ") if rank_def else f"Rank {new_rank_num}"
            band_low, _ = level_range_for_rank(new_rank_num)
            sub = new_level - band_low + 1
            # Check for planet + building unlocks at this new level, so the
            # level-up message can name the concrete "you can now build X"
            # payoff. Only announce on a level GAIN — a level drop (death XP
            # loss) is not celebratory and unlocks nothing new.
            planets_unlocked = self._check_planet_unlocks(old_level, new_level)
            buildings_unlocked = (
                self._check_building_unlocks(player, old_level, new_level)
                if new_level > old_level else []
            )
            self.notify(
                player, "rank_level_up",
                level=new_level, rank_name=rank_name, sub=sub,
                old_level=old_level,
                planets_unlocked=planets_unlocked,
                buildings_unlocked=buildings_unlocked,
            )

        # Fire rank events if rank boundary crossed
        if new_rank_num > old_rank_num:
            old_rank_def = self._get_rank_by_level(old_rank_num)
            new_rank_def = self._get_rank_by_level(new_rank_num)
            if new_rank_def:
                # NOTE: promotion does NOT auto-grant technologies (R13.1) —
                # research at a Lab is the only tech-acquisition path.
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
                # NOTE: demotion does NOT revoke researched technologies
                # (R13.2) — a paid-for tech is never taken away by rank churn.
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

        # Publish LEVEL_CHANGED for any level change (after rank-event
        # handling so reserve/restore is applied first). Owned-agent gate
        # re-evaluation is driven by this event regardless of rank boundary.
        if new_level != old_level:
            self.event_bus.publish(
                LEVEL_CHANGED,
                player=player,
                old_level=old_level,
                new_level=new_level,
            )

    def _get_rank_by_level(self, rank_num: int) -> "RankDef | None":
        """Find a RankDef by its rank number (1-12)."""
        return self.registry.get_rank_by_level(rank_num)

    def _get_next_rank(self, current_rank_num: int) -> "RankDef | None":
        """Return the next rank above current_rank_num, or None."""
        return self.registry.get_rank_by_level(current_rank_num + 1)

