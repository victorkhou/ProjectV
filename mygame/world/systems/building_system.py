"""
Building System for the RTS Combat Overworld game.

Handles construction, upgrade, and destruction logic for all building types.
Validates prerequisites, terrain, resources, and combat lockout before
allowing construction. Publishes events via the EventBus.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.8, 3.9, 3.10, 3.11,
              4.2, 4.4, 5.3, 5.4, 5.5, 5.6, 5.7
"""

from __future__ import annotations

from typing import Any, Callable

from world.data_registry import DataRegistry
from world.definitions import BuildingDef
from world.event_bus import (
    BUILDING_CONSTRUCTED,
    BUILDING_DESTROYED,
    BUILDING_UPGRADED,
    EventBus,
)

# Default maximum build range (Manhattan distance)
DEFAULT_BUILD_RANGE = 10

# Maximum building level for resource buildings
MAX_BUILDING_LEVEL = 5


def _manhattan_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Return the Manhattan distance between two coordinate pairs."""
    return abs(x1 - x2) + abs(y1 - y2)


class BuildingSystem:
    """Manages building construction, upgrades, and destruction.

    Args:
        registry: The DataRegistry holding all building definitions.
        event_bus: The EventBus for publishing game events.
        create_building_func: Optional factory callable for creating Building
            objects. Signature: ``(building_def, tile, owner) -> building``.
            If not provided, uses ``evennia.create_object`` by default.
        build_range: Maximum Manhattan distance for building placement.
        current_tick_func: Optional callable returning the current game tick.
            Defaults to returning 0 (no combat lockout).
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_building_func: Callable | None = None,
        build_range: int = DEFAULT_BUILD_RANGE,
        current_tick_func: Callable[[], int] | None = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self._create_building_func = create_building_func or self._default_create_building
        self.build_range = build_range
        self._current_tick_func = current_tick_func or (lambda: 0)

    # ------------------------------------------------------------------ #
    #  Construction
    # ------------------------------------------------------------------ #

    def construct(
        self, player: Any, tile: Any, building_abbr: str
    ) -> tuple[bool, str]:
        """Construct a building on the given tile.

        Validation chain:
            1. HQ requirement (unless building HQ)
            2. Terrain match (for resource buildings)
            3. Tile empty
            4. Build range
            5. Combat lockout
            6. Sufficient resources

        Returns:
            (success, message) tuple.
        """
        # Look up building definition
        try:
            building_def = self.registry.get_building(building_abbr)
        except KeyError:
            return False, f"Unknown building type: {building_abbr}"

        # 1. HQ requirement
        err = self._validate_hq_requirement(player, building_def)
        if err:
            return False, err

        # 2. Terrain match
        err = self._validate_terrain(tile, building_def)
        if err:
            return False, err

        # 3. Tile empty
        err = self._validate_tile_empty(tile)
        if err:
            return False, err

        # 4. Build range
        err = self._validate_build_range(player, tile)
        if err:
            return False, err

        # 5. Combat lockout
        err = self._validate_combat_lockout(player)
        if err:
            return False, err

        # 6. Sufficient resources
        err = self._validate_resources(player, building_def.cost)
        if err:
            return False, err

        # All checks passed — deduct resources
        player.deduct_resources(building_def.cost)

        # Create the building object on the tile
        building = self._create_building_func(building_def, tile, player)

        # Publish event
        self.event_bus.publish(
            BUILDING_CONSTRUCTED,
            player=player,
            building=building,
            tile=tile,
        )

        return True, f"Constructed {building_def.name} on tile."

    # ------------------------------------------------------------------ #
    #  Upgrade
    # ------------------------------------------------------------------ #

    def upgrade(self, player: Any, building: Any) -> tuple[bool, str]:
        """Upgrade a resource building to the next level.

        Validation:
            1. Building is owned by player
            2. Building is a resource building
            3. Level < MAX_BUILDING_LEVEL
            4. Sufficient resources (base_cost * target_level)

        Returns:
            (success, message) tuple.
        """
        # 1. Ownership check
        if getattr(building, "owner", None) is not player:
            return False, "You do not own this building."

        # 2. Category check — must be a resource building
        building_type = None
        if hasattr(building, "attributes"):
            building_type = building.attributes.get("building_type", default=None)
        elif hasattr(building, "db"):
            building_type = getattr(building.db, "building_type", None)

        if building_type is None:
            return False, "Cannot determine building type."

        try:
            building_def = self.registry.get_building(building_type)
        except KeyError:
            return False, f"Unknown building type: {building_type}"

        if building_def.category != "resource":
            return False, "Only resource buildings can be upgraded."

        # 3. Level check
        current_level = building.building_level
        if current_level >= MAX_BUILDING_LEVEL:
            return False, "This building is already at maximum level (5)."

        target_level = current_level + 1

        # 4. Calculate upgrade cost: base_cost * target_level
        upgrade_cost = {
            resource: amount * target_level
            for resource, amount in building_def.cost.items()
        }

        err = self._validate_resources(player, upgrade_cost)
        if err:
            return False, err

        # All checks passed — deduct resources and upgrade
        player.deduct_resources(upgrade_cost)

        old_level = current_level
        # Set the new level
        if hasattr(building, "attributes"):
            building.attributes.add("building_level", target_level)
        elif hasattr(building, "db"):
            building.db.building_level = target_level

        # Publish event
        self.event_bus.publish(
            BUILDING_UPGRADED,
            player=player,
            building=building,
            old_level=old_level,
            new_level=target_level,
        )

        return True, f"Upgraded {building_def.name} to level {target_level}."

    # ------------------------------------------------------------------ #
    #  Destruction
    # ------------------------------------------------------------------ #

    def destroy(self, building: Any, attacker: Any = None) -> None:
        """Remove a building from the game and publish the event.

        Args:
            building: The Building object to destroy.
            attacker: The player who destroyed it (if any).
        """
        tile = getattr(building, "location", None)

        # Publish event before removal
        self.event_bus.publish(
            BUILDING_DESTROYED,
            attacker=attacker,
            building=building,
            tile=tile,
        )

        # Remove the building from the tile
        if hasattr(building, "delete"):
            building.delete()

    # ------------------------------------------------------------------ #
    #  Offline protection
    # ------------------------------------------------------------------ #

    def set_player_buildings_offline(
        self, player: Any, offline: bool
    ) -> None:
        """Transition all buildings owned by a player to offline/online.

        Args:
            player: The player whose buildings to transition.
            offline: True to set offline, False to set online.
        """
        buildings = self._get_player_buildings(player)
        for building in buildings:
            if hasattr(building, "set_offline"):
                building.set_offline(offline)

    # ------------------------------------------------------------------ #
    #  Validation helpers
    # ------------------------------------------------------------------ #

    def _validate_hq_requirement(
        self, player: Any, building_def: BuildingDef
    ) -> str | None:
        """Check HQ prerequisite. Returns error message or None."""
        if not building_def.requires_hq:
            return None

        # Player must have an HQ
        if self._player_has_hq(player):
            return None

        return "You must construct a Headquarters first."

    def _validate_terrain(
        self, tile: Any, building_def: BuildingDef
    ) -> str | None:
        """Check terrain matches required_terrain. Returns error or None."""
        if building_def.required_terrain is None:
            return None

        terrain = getattr(tile, "terrain_type", None)
        if terrain == building_def.required_terrain:
            return None

        return (
            f"A {building_def.name} requires {building_def.required_terrain} terrain."
        )

    def _validate_tile_empty(self, tile: Any) -> str | None:
        """Check tile has no existing building. Returns error or None."""
        building = getattr(tile, "building", None)
        if building is None:
            return None
        return "This tile already contains a building."

    def _validate_build_range(self, player: Any, tile: Any) -> str | None:
        """Check tile is within build range. Returns error or None."""
        player_loc = getattr(player, "location", None)
        if player_loc is None:
            return None  # Can't validate without location

        # Get coordinates
        player_coords = self._get_coords(player_loc)
        tile_coords = self._get_coords(tile)

        if player_coords is None or tile_coords is None:
            return None  # Can't validate without coordinates

        px, py = player_coords
        tx, ty = tile_coords
        dist = _manhattan_distance(px, py, tx, ty)

        if dist <= self.build_range:
            return None

        return "Target tile is too far away."

    def _validate_combat_lockout(self, player: Any) -> str | None:
        """Check player is not in combat lockout. Returns error or None."""
        lockout_tick = 0
        if hasattr(player, "db"):
            lockout_tick = getattr(player.db, "combat_lockout_tick", 0) or 0
        elif hasattr(player, "attributes"):
            lockout_tick = player.attributes.get(
                "combat_lockout_tick", default=0
            ) or 0

        current_tick = self._current_tick_func()
        if lockout_tick > current_tick:
            return "Cannot build while in combat."

        return None

    def _validate_resources(
        self, player: Any, costs: dict[str, int]
    ) -> str | None:
        """Check player has sufficient resources. Returns error or None."""
        if player.has_resources(costs):
            return None

        # Build a descriptive error message
        missing = []
        for resource, needed in costs.items():
            current = player.get_resource(resource)
            if current < needed:
                missing.append(
                    f"need {needed} {resource}, have {current}"
                )
        return "Insufficient resources: " + "; ".join(missing) + "."

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _player_has_hq(self, player: Any) -> bool:
        """Check if the player owns an HQ building."""
        buildings = self._get_player_buildings(player)
        for b in buildings:
            btype = None
            if hasattr(b, "attributes"):
                btype = b.attributes.get("building_type", default=None)
            elif hasattr(b, "db"):
                btype = getattr(b.db, "building_type", None)
            if btype == "HQ":
                return True
        return False

    def _get_player_buildings(self, player: Any) -> list:
        """Return all buildings owned by the player."""
        if hasattr(player, "get_buildings"):
            return player.get_buildings()
        return []

    def _get_coords(self, obj: Any) -> tuple[int, int] | None:
        """Extract (x, y) coordinates from an object."""
        x = getattr(obj, "x", None)
        y = getattr(obj, "y", None)
        if x is not None and y is not None:
            return (int(x), int(y))
        return None

    @staticmethod
    def _default_create_building(
        building_def: BuildingDef, tile: Any, owner: Any
    ) -> Any:
        """Default building factory using evennia.create_object."""
        import evennia

        building = evennia.create_object(
            "typeclasses.objects.Building",
            key=building_def.name,
            location=tile,
        )
        building.attributes.add("building_type", building_def.abbreviation)
        building.attributes.add("owner", owner)
        building.attributes.add("building_level", 1)
        building.attributes.add("offline", False)
        building.attributes.add("hp", building_def.max_health)
        building.attributes.add("hp_max", building_def.max_health)
        return building
