"""
Equipment System for the RTS Combat Overworld game.

Manages equipment building production: Armory (AA) and Armorer (AR)
buildings produce GameItem instances each tick from their production_map.

"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from world.data_registry import DataRegistry
from world.definitions import ItemDef
from world.event_bus import EventBus
from world.systems.base_system import BaseSystem

logger = logging.getLogger("mygame.equipment_system")

# Building abbreviations that are equipment buildings
EQUIPMENT_BUILDING_TYPES = ("AA", "AR")


class EquipmentSystem(BaseSystem):
    """Manages Armory and Armorer GameItem generation.

    Each tick, active equipment buildings look up their producible items
    via ``registry.get_items_for_building(building_abbr)``, select one,
    create a GameItem-like object, and add it to the owner's inventory.

    Args:
        registry: The DataRegistry holding item/building definitions.
        event_bus: The EventBus for publishing game events.
        create_item_func: Optional factory callable for creating item
            objects. Signature: ``(item_def, owner) -> item``.
            If not provided, uses a default that creates a simple
            dict-like item.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_item_func: Callable[[ItemDef, Any], Any] | None = None,
    ) -> None:
        super().__init__(registry, event_bus)
        self._create_item_func = create_item_func or self._default_create_item

    # ------------------------------------------------------------------ #
    #  Production
    # ------------------------------------------------------------------ #

    def process_production(self, active_buildings: list) -> None:
        """Process equipment production for active equipment buildings.

        For each active equipment building (AA or AR, not offline):
            - Look up producible items via registry
            - Select one item from the list
            - Create a GameItem-like object
            - Add to owner's inventory

        Args:
            active_buildings: List of Building objects to process.
        """
        for building in active_buildings:
            # Skip offline buildings
            if getattr(building, "is_offline", False):
                continue

            # Get building type
            building_type = self._get_building_type(building)
            if building_type not in EQUIPMENT_BUILDING_TYPES:
                continue

            # Get owner
            owner = getattr(building, "owner", None)
            if owner is None:
                continue

            # Look up producible items
            item_defs = self.registry.get_items_for_building(building_type)
            if not item_defs:
                continue

            # Select one item (random choice)
            item_def = random.choice(item_defs)

            # Create the item and add to owner's inventory
            item = self._create_item_func(item_def, owner)

            logger.info(
                "Equipment building %s produced %s for %s",
                building_type,
                item_def.name,
                getattr(owner, "key", "?"),
            )

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_building_type(building: Any) -> str | None:
        """Read the building_type string from a building."""
        from world.utils import get_building_type
        return get_building_type(building)

    @staticmethod
    def _default_create_item(item_def: ItemDef, owner: Any) -> dict:
        """Default item factory — creates a simple dict representation.

        In a real Evennia environment this would use create_object to
        make a GameItem typeclass instance. For testing and lightweight
        use, returns a dict with the item's properties.
        """
        item = {
            "key": item_def.key,
            "name": item_def.name,
            "slot": item_def.slot,
            "stat_modifiers": dict(item_def.stat_modifiers),
            "ammo_cost": dict(item_def.ammo_cost) if item_def.ammo_cost else None,
            "classification": item_def.classification,
            "required_rank": item_def.required_rank,
        }
        # Add to owner's inventory if possible
        if hasattr(owner, "db") and hasattr(owner.db, "inventory"):
            inv = owner.db.inventory
            if inv is None:
                inv = []
                owner.db.inventory = inv
            inv.append(item)
        elif hasattr(owner, "_inventory"):
            owner._inventory.append(item)
        return item
