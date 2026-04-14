"""
Agent behavior scripts — Evennia Scripts attached to NPC agent objects.

Each script class corresponds to an agent role and implements
``at_repeat()`` with the role's per-tick logic.  Scripts have
``interval = 0`` so they are driven by the GameTickScript rather
than self-timed.

Requirements: 9.1, 10.1, 10.5, 10.6, 11.1, 11.3, 12.1, 12.3
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from evennia.scripts.scripts import DefaultScript
except ImportError:
    # Fallback for test environments where Evennia is not available.
    class DefaultScript:  # type: ignore[no-redef]
        """Minimal stub so the module can be imported outside Evennia."""

        key = ""
        desc = ""
        interval = 0
        persistent = True
        obj: Any = None

        def at_script_creation(self) -> None: ...
        def at_repeat(self) -> None: ...

logger = logging.getLogger("mygame.agent_scripts")


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #

def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Read an Evennia attribute from *obj*, with fallback."""
    from world.utils import get_obj_attr
    return get_obj_attr(obj, key, default)


def _set_attr(obj: Any, key: str, value: Any) -> None:
    """Write an Evennia attribute on *obj*, with fallback."""
    from world.utils import set_obj_attr
    set_obj_attr(obj, key, value)


# ------------------------------------------------------------------ #
#  HarvesterScript  (Req 9.1, 9.2, 9.3, 9.4)
# ------------------------------------------------------------------ #

class HarvesterScript(DefaultScript):
    """Produces resources each tick when attached to an NPC assigned to an Extractor.

    The NPC's ``role_target`` must point to an Extractor building.
    Production is scaled by the Extractor's level using the formula::

        base_rate × (1 + 0.25 × (level - 1))

    Produced resources are added to the Extractor's local inventory,
    respecting its storage capacity.
    """

    def at_script_creation(self) -> None:
        self.key = "harvester_script"
        self.desc = "Harvester agent production loop"
        self.interval = 0  # driven by GameTickScript
        self.persistent = True

    def at_repeat(self) -> None:
        npc = self.obj
        if npc is None:
            return

        # Agent must not be incapacitated
        if getattr(getattr(npc, "db", None), "incapacitated", False):
            return

        building = getattr(getattr(npc, "db", None), "role_target", None)
        if building is None:
            return

        # Determine building type — must be an Extractor (resource category)
        building_type = _get_attr(building, "building_type")
        if building_type != "EX":
            return

        # Determine resource type from the terrain tile the Extractor sits on
        resource_type = self._resolve_resource_type(building)
        if not resource_type:
            return

        # Calculate production amount
        level = _get_attr(building, "building_level", 1) or 1
        base_rate = self._get_base_rate()
        production = base_rate * (1 + 0.25 * (level - 1))
        production_int = max(1, int(production)) if base_rate > 0 else 0
        if production_int <= 0:
            return

        # Add to Extractor inventory (respects capacity)
        from world.systems.resource_system import ResourceSystem

        added = ResourceSystem.add_to_extractor_inventory(
            building, resource_type, production_int, level
        )

        if added > 0:
            logger.debug(
                "Harvester on %s produced %d %s (level %d)",
                building, added, resource_type, level,
            )

    # -- internal helpers ---------------------------------------------- #

    @staticmethod
    def _resolve_resource_type(building: Any) -> str | None:
        """Determine the resource type for an Extractor.

        Checks the building's stored ``resource_type`` attribute first,
        then falls back to reading the terrain tile's resource node.
        """
        # Explicit attribute on the building
        rt = _get_attr(building, "resource_type")
        if rt:
            return rt

        # Fall back to the terrain tile the building sits on
        tile = getattr(building, "location", None)
        if tile is None:
            return None

        # Try resource_node_data dict
        node = _get_attr(tile, "resource_node_data")
        if isinstance(node, dict):
            return node.get("resource_type")

        # Try direct terrain attribute
        return _get_attr(tile, "resource_type")

    @staticmethod
    def _get_base_rate() -> int:
        """Return the base harvest rate from the DataRegistry balance config.

        Falls back to a sensible default (5) if the registry is
        unavailable.
        """
        try:
            from world.data_registry import DataRegistry

            registry = DataRegistry.get_instance()
            return registry.balance.gather_amount
        except Exception:
            return 5


# ------------------------------------------------------------------ #
#  EngineerScript  (Req 10.1, 10.5, 10.6)
# ------------------------------------------------------------------ #

class EngineerScript(DefaultScript):
    """Progresses construction and research timers autonomously.

    The NPC's ``role_target`` must point to a building that has an
    active ``construction_total`` (construction/upgrade) or a
    ``research_total`` (Lab research).  Each tick, the script
    increments the corresponding progress counter.
    """

    def at_script_creation(self) -> None:
        self.key = "engineer_script"
        self.desc = "Engineer agent construction/research loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        npc = self.obj
        if npc is None:
            return

        if getattr(getattr(npc, "db", None), "incapacitated", False):
            return

        building = getattr(getattr(npc, "db", None), "role_target", None)
        if building is None:
            return

        # Try construction progress first
        construction_total = _get_attr(building, "construction_total", 0) or 0
        if construction_total > 0:
            progress = _get_attr(building, "construction_progress", 0) or 0
            if progress < construction_total:
                progress += 1
                _set_attr(building, "construction_progress", progress)
                if progress >= construction_total:
                    self._complete_construction(building)
                return

        # Try research progress (Lab)
        research_total = _get_attr(building, "research_total", 0) or 0
        if research_total > 0:
            research_progress = _get_attr(building, "research_progress", 0) or 0
            if research_progress < research_total:
                research_progress += 1
                _set_attr(building, "research_progress", research_progress)
                if research_progress >= research_total:
                    self._complete_research(building)
                return

    @staticmethod
    def _complete_construction(building: Any) -> None:
        """Finalise a completed construction."""
        logger.debug("Engineer completed construction on %s", building)
        # Mark construction as done (progress == total already set)
        # The BuildingSystem's process_agent_construction handles the
        # full completion flow; this is a safety net for direct calls.

    @staticmethod
    def _complete_research(building: Any) -> None:
        """Finalise a completed research project."""
        logger.debug("Engineer completed research on %s", building)
        # Placeholder — full research completion logic will be wired
        # when the technology system is integrated.


# ------------------------------------------------------------------ #
#  GuardScript  (Req 12.1)
# ------------------------------------------------------------------ #

class GuardScript(DefaultScript):
    """Activates Turret auto-attack when attached to an NPC assigned to a Turret.

    Placeholder — full combat integration in a later phase.
    """

    def at_script_creation(self) -> None:
        self.key = "guard_script"
        self.desc = "Guard agent turret activation loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        # Placeholder: activate Turret auto-attack on enemies in range.
        # Full implementation requires the CombatEngine turret logic.
        pass


# ------------------------------------------------------------------ #
#  ScoutScript  (Req 12.3)
# ------------------------------------------------------------------ #

class ScoutScript(DefaultScript):
    """Extends Radar vision radius when attached to an NPC assigned to a Radar.

    Placeholder — vision radius extension will be wired when the
    map rendering integration is complete.
    """

    def at_script_creation(self) -> None:
        self.key = "scout_script"
        self.desc = "Scout agent radar vision loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        # Placeholder: extend Radar vision radius for the owning player.
        pass


# ------------------------------------------------------------------ #
#  SoldierScript  (Req 11.1)
# ------------------------------------------------------------------ #

class SoldierScript(DefaultScript):
    """Participates in army combat calculations.

    Placeholder — army combat logic will be implemented with the
    CombatEngine expansion.
    """

    def at_script_creation(self) -> None:
        self.key = "soldier_script"
        self.desc = "Soldier agent combat loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        # Placeholder: participate in army combat calculations.
        pass


# ------------------------------------------------------------------ #
#  MedicScript  (Req 11.3)
# ------------------------------------------------------------------ #

class MedicScript(DefaultScript):
    """Heals soldiers after combat and reduces respawn time at Medbay.

    Placeholder — healing and respawn reduction will be implemented
    with the CombatEngine and Medbay integration.
    """

    def at_script_creation(self) -> None:
        self.key = "medic_script"
        self.desc = "Medic agent healing loop"
        self.interval = 0
        self.persistent = True

    def at_repeat(self) -> None:
        # Placeholder: heal soldiers after combat, reduce respawn time
        # at Medbay.
        pass


# ------------------------------------------------------------------ #
#  Script class lookup
# ------------------------------------------------------------------ #

#: Maps role name → Script class for use by AgentSystem when attaching
#: behavior scripts to NPC agents.
ROLE_SCRIPT_MAP: dict[str, type] = {
    "harvester": HarvesterScript,
    "engineer": EngineerScript,
    "guard": GuardScript,
    "scout": ScoutScript,
    "soldier": SoldierScript,
    "medic": MedicScript,
}
