"""
Evennia-backed BuildingFactory and MovingEntityRepository implementations.

Homes for the ``evennia.create_object`` building factory and the
``search_object_by_tag`` moving-NPC recovery scan, so ``BuildingSystem`` and
``MovementSystem`` depend on the ports rather than importing Evennia.
"""

from __future__ import annotations

import logging
from typing import Any

from world.core.ports.entity_repository import (
    BuildingFactory,
    MovingEntityRepository,
)

logger = logging.getLogger("evennia.world.adapters.building")


class EvenniaBuildingFactory(BuildingFactory):
    """Creates + places + indexes Building objects via Evennia.

    Body lifted verbatim from the former
    ``BuildingSystem._default_create_building`` so behavior is preserved.
    """

    def create_building(
        self,
        building_def: Any,
        tile: Any,
        owner: Any,
        x: int | None = None,
        y: int | None = None,
    ) -> Any:
        import evennia

        building = evennia.create_object(
            "typeclasses.objects.Building",
            key=building_def.name,
            location=tile,
        )
        if x is not None and y is not None:
            building.db.coord_x = x
            building.db.coord_y = y
            # create_object added the building to PlanetRoom.contents, but
            # at_object_receive saw coord_x=None; register it now that coords
            # are set.
            if hasattr(tile, "coord_index"):
                tile.coord_index.add(building, x, y)
        building.attributes.add("building_type", building_def.abbreviation)
        building.attributes.add("owner", owner)
        building.attributes.add("building_level", 1)
        building.attributes.add("offline", False)
        # Open buildings can be hit by ranged weapons and turrets; closed ones
        # only by adjacent (melee) player/agent attacks. Defaults to open so
        # behavior is unchanged unless a building is explicitly closed.
        building.attributes.add("open", True)
        building.attributes.add("hp", building_def.max_health)
        building.attributes.add("hp_max", building_def.max_health)
        # Tag is auto-set by Building.at_object_creation via GameEntity.
        # Phase 1 construction timer & inventory attributes.
        building.attributes.add("assigned_agent", None)
        building.attributes.add("construction_progress", 0)
        building.attributes.add("construction_total", 0)
        building.attributes.add("under_construction", False)
        return building


class EvenniaMovingEntityRepository(MovingEntityRepository):
    """Recovers moving NPCs from Evennia's tag index after a restart."""

    def find_moving_npcs(self) -> list[Any]:
        try:
            from evennia.utils.search import search_object_by_tag

            result = []
            for npc in search_object_by_tag("npc", category="object_type"):
                queue = getattr(getattr(npc, "db", None), "movement_queue", None)
                if queue:
                    result.append(npc)
            return result
        except Exception:
            logger.exception("find_moving_npcs failed")
            return []
