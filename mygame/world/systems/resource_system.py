"""
Resource System for the RTS Combat Overworld game.

Handles manual resource gathering from terrain nodes, automated
production from resource buildings, and depleted node respawn cycles.

Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 5.2, 5.8, 15.1, 15.2,
              15.3, 15.4
"""

from __future__ import annotations

from typing import Any

from world.data_registry import DataRegistry
from world.event_bus import RESOURCE_GATHERED, EventBus


class ResourceSystem:
    """Manages resource gathering, building production, and node respawns.

    Args:
        registry: The DataRegistry holding terrain/building definitions
            and balance configuration.
        event_bus: The EventBus for publishing game events.
    """

    def __init__(self, registry: DataRegistry, event_bus: EventBus) -> None:
        self.registry = registry
        self.event_bus = event_bus

    # ------------------------------------------------------------------ #
    #  Manual harvest
    # ------------------------------------------------------------------ #

    def harvest(self, player: Any, tile: Any) -> tuple[bool, str]:
        """Harvest a resource from the tile's resource node.

        Flow:
            1. Check tile has a resource_node
            2. Check node is not depleted
            3. Determine resource_type from the node
            4. Add gather_amount to player
            5. Mark node depleted, set respawn_counter
            6. Publish resource_gathered event

        Returns:
            (success, message) tuple.
        """
        # Read resource node data from tile
        node = self._get_resource_node(tile)
        if node is None:
            return False, "No resource node on this tile."

        if node.get("depleted", False):
            return False, "This resource node is depleted."

        resource_type = node.get("resource_type")
        if not resource_type:
            return False, "This resource node has no resource type."

        # Determine yield amount from balance config
        gather_amount = self.registry.balance.gather_amount

        # Add resources to player
        player.add_resource(resource_type, gather_amount)

        # Mark node as depleted and set respawn counter
        node["depleted"] = True
        node["respawn_counter"] = self.registry.balance.resource_respawn_ticks
        self._set_resource_node(tile, node)

        # Publish event
        self.event_bus.publish(
            RESOURCE_GATHERED,
            player=player,
            resource_type=resource_type,
            amount=gather_amount,
            tile=tile,
        )

        return True, f"Harvested {gather_amount} {resource_type}."

    # ------------------------------------------------------------------ #
    #  Automated production from resource buildings
    # ------------------------------------------------------------------ #

    def process_production(self, active_buildings: list) -> None:
        """Process resource production for active resource buildings.

        For each active resource building (not offline, owner online):
            - Look up building_level
            - Get yield from balance.production_scaling[level]
            - Determine resource type from building definition's produces field
            - Add yield to owner's resources

        Args:
            active_buildings: List of Building objects to process.
        """
        for building in active_buildings:
            # Skip offline buildings
            if getattr(building, "is_offline", False):
                continue

            # Get building type
            building_type = self._get_building_type(building)
            if not building_type:
                continue

            # Look up building definition
            try:
                building_def = self.registry.get_building(building_type)
            except KeyError:
                continue

            # Only process resource-category buildings
            if building_def.category != "resource":
                continue

            # Determine what resource this building produces
            resource_type = building_def.produces
            if not resource_type:
                continue

            # Get owner
            owner = getattr(building, "owner", None)
            if owner is None:
                continue

            # Get building level and production yield
            level = self._get_building_level(building)
            production_yield = self.registry.balance.production_scaling.get(level, 0)

            if production_yield > 0:
                owner.add_resource(resource_type, production_yield)

    # ------------------------------------------------------------------ #
    #  Resource node respawn
    # ------------------------------------------------------------------ #

    def process_respawns(self, tiles: list) -> None:
        """Process respawn counters for depleted resource nodes.

        For each tile with a depleted resource node:
            - Decrement respawn_counter
            - If counter reaches 0, set depleted=False

        Args:
            tiles: List of OverworldRoom tiles to process.
        """
        for tile in tiles:
            node = self._get_resource_node(tile)
            if node is None:
                continue

            if not node.get("depleted", False):
                continue

            counter = node.get("respawn_counter", 0)
            counter -= 1

            if counter <= 0:
                node["depleted"] = False
                node["respawn_counter"] = 0
            else:
                node["respawn_counter"] = counter

            self._set_resource_node(tile, node)

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_resource_node(tile: Any) -> dict | None:
        """Read the resource node dict from a tile."""
        # Try Evennia Attribute handler first
        if hasattr(tile, "attributes") and hasattr(tile.attributes, "get"):
            data = tile.attributes.get("resource_node_data", default=None)
            if data is not None:
                return data

        # Fallback: try .resource_node property
        node = getattr(tile, "resource_node", None)
        return dict(node) if node else None

    @staticmethod
    def _set_resource_node(tile: Any, node: dict) -> None:
        """Write the resource node dict back to a tile."""
        if hasattr(tile, "attributes") and hasattr(tile.attributes, "add"):
            tile.attributes.add("resource_node_data", node)
        elif hasattr(tile, "db"):
            tile.db.resource_node_data = node

    @staticmethod
    def _get_building_type(building: Any) -> str | None:
        """Read the building_type string from a building."""
        if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
            return building.attributes.get("building_type", default=None)
        if hasattr(building, "db"):
            return getattr(building.db, "building_type", None)
        return None

    @staticmethod
    def _get_building_level(building: Any) -> int:
        """Read the building level from a building."""
        if hasattr(building, "building_level"):
            return building.building_level
        if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
            return building.attributes.get("building_level", default=1)
        if hasattr(building, "db"):
            return getattr(building.db, "building_level", 1)
        return 1
