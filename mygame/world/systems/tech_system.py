"""
Tech Lab System for the RTS Combat Overworld game.

Manages technology research: listing available techs by rank, starting
research with resource deduction, tick-based timer countdown, and
applying completed technology effects.

"""

from __future__ import annotations

import logging
from typing import Any

from world.data_registry import DataRegistry
from world.definitions import TechnologyDef
from world.event_bus import TECHNOLOGY_RESEARCHED, EventBus
from world.systems.base_system import BaseSystem

logger = logging.getLogger("mygame.tech_system")


class TechLabSystem(BaseSystem):
    """Manages technology research at Tech Labs.

    Research timers are stored on the Tech_Lab building as
    ``building.db.research_timers`` (dict of tech_key -> {ticks_remaining, player}).

    Args:
        registry: The DataRegistry holding technology/rank definitions.
        event_bus: The EventBus for publishing game events.
    """

    def __init__(self, registry: DataRegistry, event_bus: EventBus) -> None:
        super().__init__(registry, event_bus)
        # Track active research: list of {tech_key, player, ticks_remaining, tech_lab}
        self._active_research: list[dict] = []

    # ------------------------------------------------------------------ #
    #  List available technologies
    # ------------------------------------------------------------------ #

    def list_available(self, player: Any) -> list[TechnologyDef]:
        """Return technologies available at the player's current rank.

        Filters out already-researched technologies.

        Args:
            player: The player to query.

        Returns:
            List of TechnologyDef objects available for research.
        """
        rank_level = self._get_player_level(player)
        from world.systems.rank_system import rank_from_level
        rank_num = rank_from_level(rank_level)
        all_techs = self.registry.get_technologies_for_rank(rank_num)

        researched = self._get_researched_techs(player)
        return [t for t in all_techs if t.key not in researched]

    # ------------------------------------------------------------------ #
    #  Start research
    # ------------------------------------------------------------------ #

    def start_research(
        self, player: Any, tech_key: str, tech_lab: Any = None
    ) -> tuple[bool, str]:
        """Start researching a technology.

        Validation:
            1. Tech key exists in registry
            2. Player rank meets required_rank
            3. Tech not already researched
            4. Player has sufficient resources

        On success:
            - Deduct resources
            - Add to active research queue

        Returns:
            (success, message) tuple.
        """
        # 1. Look up technology definition
        tdef = self.registry.technologies.get(tech_key)
        if tdef is None:
            return False, f"Unknown technology: {tech_key}"

        # 2. Rank check — compare player's derived rank against required rank
        player_level = self._get_player_level(player)
        from world.systems.rank_system import player_meets_rank
        if not player_meets_rank(player_level, tdef.required_rank, self.registry):
            return False, (
                f"Requires rank {tdef.required_rank} "
                f"(you are level {player_level})."
            )

        # 3. Already researched check
        researched = self._get_researched_techs(player)
        if tech_key in researched:
            return False, f"Technology {tech_key} is already researched."

        # 4. Already in progress check
        for entry in self._active_research:
            if entry["tech_key"] == tech_key and entry["player"] is player:
                return False, f"Technology {tech_key} is already being researched."

        # 5. Resource check and deduction
        if tdef.resource_cost:
            if not player.has_resources(tdef.resource_cost):
                # Use the shared have/need breakdown so this reads identically to
                # building construction/upgrade and agent training.
                from world.utils import format_insufficient_resources
                return False, format_insufficient_resources(
                    player, tdef.resource_cost
                )
            player.deduct_resources(tdef.resource_cost)

        # Add to active research
        self._active_research.append({
            "tech_key": tech_key,
            "player": player,
            "ticks_remaining": tdef.research_ticks,
            "tech_lab": tech_lab,
        })

        logger.info(
            "Started research %s for %s (%d ticks)",
            tech_key, getattr(player, "key", "?"), tdef.research_ticks,
        )

        return True, (
            f"Started researching {tdef.name} "
            f"({tdef.research_ticks} ticks)."
        )

    # ------------------------------------------------------------------ #
    #  Tick processing
    # ------------------------------------------------------------------ #

    def process_tick(self) -> None:
        """Decrement research timers and apply completed technologies.

        For each active research entry:
            - Decrement ticks_remaining
            - If ticks_remaining <= 0, apply the technology and remove
        """
        completed = []
        remaining = []

        for entry in self._active_research:
            entry["ticks_remaining"] -= 1
            if entry["ticks_remaining"] <= 0:
                completed.append(entry)
            else:
                remaining.append(entry)

        self._active_research = remaining

        for entry in completed:
            tech_key = entry["tech_key"]
            player = entry["player"]

            tdef = self.registry.technologies.get(tech_key)
            if tdef is None:
                continue

            # Apply the technology
            self.apply_technology(player, tdef)

            # Add to researched set
            researched = self._get_researched_techs(player)
            researched.add(tech_key)
            self._set_researched_techs(player, researched)

            # Publish event
            self.event_bus.publish(
                TECHNOLOGY_RESEARCHED,
                player=player,
                technology=tdef,
            )

            logger.info(
                "Research completed: %s for %s",
                tech_key, getattr(player, "key", "?"),
            )

    # ------------------------------------------------------------------ #
    #  Apply technology effects
    # ------------------------------------------------------------------ #

    def apply_technology(self, player: Any, tech_def: TechnologyDef) -> None:
        """Apply a completed technology's effect to the player (R13.3).

        Writes the technology's payload into ``player.db.tech_bonuses`` — a
        cumulative bonus dict read by downstream consumers (CombatEngine,
        FogOfWar, building-hp, production). Multiplicative effects
        (``production_multiplier``) compose; all others are additive.

        The five shipped payload keys and their consumers:
        - ``building_hp``            → building hp_max computation
        - ``damage``                 → CombatEngine attacker bonus
        - ``damage_reduction``       → CombatEngine armor path
        - ``sight_range``            → FogOfWar player vision radius
        - ``production_multiplier``  → equipment/extractor production path

        Args:
            player: The player to apply the effect to.
            tech_def: The technology definition.
        """
        if not tech_def.effect_value:
            return
        self._apply_tech_effect(player, tech_def)

    @staticmethod
    def _apply_tech_effect(player: Any, tech_def: TechnologyDef) -> None:
        """Write tech effects into db.tech_bonuses (R13.3, D5)."""
        effect = tech_def.effect_value
        if not isinstance(effect, dict):
            return
        db = getattr(player, "db", None)
        if db is None:
            return
        bonuses = dict(getattr(db, "tech_bonuses", None) or {})
        for key, value in effect.items():
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if key == "production_multiplier":
                bonuses[key] = bonuses.get(key, 1.0) * value
            else:
                bonuses[key] = bonuses.get(key, 0) + value
        db.tech_bonuses = bonuses

    def recompute_tech_bonuses(self, player: Any) -> None:
        """Rebuild db.tech_bonuses from scratch out of researched_techs (R13.5).

        ``db.tech_bonuses`` is fully derived state, so it can always be
        recomputed: clear it, then re-apply every researched tech's effect.
        This is the grandfathering path — players who received techs from the
        old rank auto-grant (which never wrote bonuses) gain the real effects
        on their next login recompute. Unknown/stale tech keys are skipped.
        """
        db = getattr(player, "db", None)
        if db is None:
            return
        db.tech_bonuses = {}
        for tech_key in self._get_researched_techs(player):
            tdef = self.registry.technologies.get(tech_key)
            if tdef is not None and tdef.effect_value:
                self._apply_tech_effect(player, tdef)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_player_level(player: Any) -> int:
        """Read the player's level (1-100). See ``world.utils.get_player_level``."""
        from world.utils import get_player_level
        return get_player_level(player, default=0)

    @staticmethod
    def _get_researched_techs(player: Any) -> set:
        """Read the player's researched_techs set."""
        if hasattr(player, "db"):
            techs = getattr(player.db, "researched_techs", None)
            if techs is None:
                techs = set()
                player.db.researched_techs = techs
            return set(techs)
        return set()

    @staticmethod
    def _set_researched_techs(player: Any, techs: set) -> None:
        """Write the player's researched_techs set."""
        if hasattr(player, "db"):
            player.db.researched_techs = techs
