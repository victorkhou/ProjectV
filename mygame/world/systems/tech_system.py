"""
Tech Lab System for the RTS Combat Overworld game.

Manages technology research: listing available techs by rank, starting
research with resource deduction, tick-based timer countdown, and
applying completed technology effects.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
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
        try:
            required_rank = self.registry.get_rank_by_name(tdef.required_rank)
            from world.systems.rank_system import rank_from_level
            player_rank = rank_from_level(player_level)
            if player_rank < required_rank.level:
                return False, (
                    f"Requires rank {tdef.required_rank} "
                    f"(you are level {player_level})."
                )
        except (KeyError, ImportError):
            pass  # If rank not found, allow research

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
                missing = []
                for r, needed in tdef.resource_cost.items():
                    current = player.get_resource(r)
                    if current < needed:
                        missing.append(f"need {needed} {r}, have {current}")
                return False, "Insufficient resources: " + "; ".join(missing) + "."
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
        """Apply a completed technology's effect to the player.

        Supported effect_types:
            - stat_bonus: Modify a player stat (e.g. max_hp, damage)
            - building_unlock: Unlock a building type
            - item_unlock: Unlock an item type

        Args:
            player: The player to apply the effect to.
            tech_def: The technology definition.
        """
        if tech_def.effect_type == "stat_bonus" and tech_def.effect_value:
            self._apply_stat_bonus(player, tech_def.effect_value)
        elif tech_def.effect_type == "building_unlock":
            # Building unlocks are handled by the rank/tech tree system
            pass
        elif tech_def.effect_type == "item_unlock":
            # Item unlocks are handled by the rank/tech tree system
            pass

    @staticmethod
    def _apply_stat_bonus(player: Any, effect_value: Any) -> None:
        """Apply a stat bonus to the player."""
        if not isinstance(effect_value, dict):
            return
        stat = effect_value.get("stat", "")
        bonus = effect_value.get("bonus", 0)
        if not stat or not bonus:
            return

        if stat == "max_hp" and hasattr(player, "db"):
            current_max = getattr(player.db, "hp_max", 100) or 100
            player.db.hp_max = current_max + bonus
            # Also increase current HP by the bonus
            current_hp = getattr(player.db, "hp", current_max) or current_max
            player.db.hp = current_hp + bonus

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_player_level(player: Any) -> int:
        """Read the player's level (1-60). See ``world.utils.get_player_level``."""
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
