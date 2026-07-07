"""
Building System for the RTS Combat Overworld game.

Handles construction, upgrade, and destruction logic for all building types.
Validates prerequisites, terrain, resources, and combat lockout before
allowing construction. Publishes events via the EventBus.

"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from world.core.ports.entity_repository import BuildingFactory
    from world.core.ports.terrain_provider import TerrainProvider

from world.data_registry import DataRegistry
from world.definitions import BuildingDef
from world.event_bus import (
    BUILDING_CONSTRUCTED,
    BUILDING_DESTROYED,
    BUILDING_UPGRADED,
    CONSTRUCTION_STARTED,
    CONSTRUCTION_COMPLETED,
    EventBus,
)
from world.systems.base_system import BaseSystem
from world.utils import get_building_attr as _get_building_attr_shared
from world.utils import set_building_attr as _set_building_attr_shared
from world.constants import (
    CONSTRUCTION_PROGRESS_INTERVAL,
    MAX_BUILDING_LEVEL,
    HEADQUARTERS,
    REQUIRES_RESOURCE_TERRAIN,
    UPGRADABLE,
)

# Default maximum build range (Manhattan distance)
DEFAULT_BUILD_RANGE = 10

# ``MAX_BUILDING_LEVEL`` is imported from ``world.constants`` (the single
# structural source) and re-exported here for backward compatibility with
# callers/tests that import it from this module.
__all__ = ["BuildingSystem", "MAX_BUILDING_LEVEL", "DEFAULT_BUILD_RANGE"]


def _manhattan_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Return the Manhattan distance between two coordinate pairs."""
    return abs(x1 - x2) + abs(y1 - y2)


class BuildingSystem(BaseSystem):
    """Manages building construction, upgrades, and destruction.

    Args:
        registry: The DataRegistry holding all building definitions.
        event_bus: The EventBus for publishing game events.
        create_building_func: Optional factory callable for creating Building
            objects. Signature: ``(building_def, tile, owner) -> building``.
            Back-compat seam; when given it overrides *building_factory*.
        build_range: Maximum Manhattan distance for building placement.
        current_tick_func: Optional callable returning the current game tick.
            Defaults to returning 0 (no combat lockout).
        building_factory: Optional :class:`BuildingFactory` for object creation.
            Defaults to the Evennia adapter.
        terrain_provider: Optional :class:`TerrainProvider` for Extractor
            placement validation. Defaults to reading the game_systems global
            (legacy fallback) until injected at the composition root.
    """

    def __init__(
        self,
        registry: DataRegistry,
        event_bus: EventBus,
        create_building_func: Callable | None = None,
        build_range: int = DEFAULT_BUILD_RANGE,
        current_tick_func: Callable[[], int] | None = None,
        building_factory: "BuildingFactory | None" = None,
        terrain_provider: "TerrainProvider | None" = None,
    ) -> None:
        super().__init__(registry, event_bus)
        # Building factory port (lazy Evennia-adapter default keeps the fast
        # unit-test suite working; a raw create_building_func still overrides it
        # for back-compat).
        from world.adapters.evennia_building_repository import EvenniaBuildingFactory

        self._factory: "BuildingFactory" = building_factory or EvenniaBuildingFactory()
        self._create_building_func = create_building_func or self._factory.create_building
        self._terrain_provider = terrain_provider
        self.build_range = build_range
        self._current_tick_func = current_tick_func or (lambda: 0)

    def set_terrain_provider(self, terrain_provider: "TerrainProvider") -> None:
        """Inject the terrain provider after construction.

        The per-planet terrain generators are built later than the systems at
        the composition root, so ``game_init`` wires the provider here once the
        generators exist. Until then Extractor validation uses the
        ``_legacy_terrain_provider`` fallback.
        """
        self._terrain_provider = terrain_provider

    # ------------------------------------------------------------------ #
    #  Construction
    # ------------------------------------------------------------------ #

    def _validate_construction(
        self, player: Any, tile: Any, building_abbr: str,
        x: int | None = None, y: int | None = None,
    ) -> tuple[BuildingDef | None, str | None]:
        """Run the full construction validation chain.

        *x*/*y* are optional target coordinates for PlanetRoom-based
        placement.  They are forwarded to validators that need them.

        Returns (building_def, None) on success, or (None, error_message) on failure.
        """
        # Accept either the abbreviation (EX) or the full name (extractor).
        building_def = self.registry.resolve_building(building_abbr)
        if building_def is None:
            return None, f"Unknown building type: {building_abbr}"

        for validator in [
            lambda: self._validate_hq_requirement(player, building_def),
            lambda: self._validate_one_hq_per_planet(player, building_def, tile),
            lambda: self._validate_rank_requirement(player, building_def),
            lambda: self._validate_terrain(tile, building_def),
            lambda: self._validate_extractor_terrain(tile, building_def, x=x, y=y),
            lambda: self._validate_tile_empty(tile, x=x, y=y),
            lambda: self._validate_build_range(player, tile, x=x, y=y),
            lambda: self._validate_combat_lockout(player),
            lambda: self._validate_resources(player, building_def.cost),
        ]:
            err = validator()
            if err:
                return None, err

        return building_def, None

    def _call_create_building(
        self, building_def: BuildingDef, tile: Any, owner: Any,
        x: int | None = None, y: int | None = None,
    ) -> Any:
        """Call the building factory, forwarding x/y if supported."""
        if x is not None and y is not None:
            try:
                return self._create_building_func(building_def, tile, owner, x=x, y=y)
            except TypeError:
                # Factory doesn't accept x/y — call without and set coords after
                building = self._create_building_func(building_def, tile, owner)
                if hasattr(building, "db"):
                    building.db.coord_x = x
                    building.db.coord_y = y
                elif hasattr(building, "attributes"):
                    building.attributes.add("coord_x", x)
                    building.attributes.add("coord_y", y)
                # Register in coordinate index (at_object_receive missed it)
                if hasattr(tile, "coord_index"):
                    tile.coord_index.add(building, x, y)
                return building
        return self._create_building_func(building_def, tile, owner)

    def construct(
        self, player: Any, tile: Any, building_abbr: str,
        x: int | None = None, y: int | None = None,
    ) -> tuple[bool, str]:
        """Construct a building on the given tile (instant, for testing/admin).

        Returns:
            (success, message) tuple.
        """
        building_def, err = self._validate_construction(player, tile, building_abbr, x=x, y=y)
        if err:
            return False, err

        player.deduct_resources(building_def.cost)
        building = self._call_create_building(building_def, tile, player, x=x, y=y)

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

    def get_upgrade_cost(self, building_def: BuildingDef, target_level: int) -> dict[str, int]:
        """Calculate upgrade cost: base_cost × COST_BASE^(target_level - 1).

        Exponential scaling makes higher levels increasingly expensive,
        creating the resource sink that drives agent utilization. The base is
        the hot-tunable ``balance.upgrade_cost_base``.
        """
        multiplier = self.registry.balance.upgrade_cost_base ** (target_level - 1)
        return {res: amt * multiplier for res, amt in building_def.cost.items()}

    def get_upgrade_time(self, building_def: BuildingDef, target_level: int) -> int:
        """Calculate upgrade time: build_time × TIME_BASE^(target_level - 1).

        Exponential scaling makes higher levels take significantly longer,
        making Engineer agents essential for mid/late-game upgrades. The base
        is the hot-tunable ``balance.upgrade_time_base``.
        """
        base = self.registry.balance.upgrade_time_base
        return int(building_def.build_time_seconds * (base ** (target_level - 1)))

    def start_upgrade(
        self, player: Any, building: Any
    ) -> tuple[bool, str]:
        """Begin a timed upgrade requiring player active-presence.

        Uses the same active-presence mechanic as construction: the
        player must stay on the tile for progress to continue. An
        Engineer agent can also progress the upgrade autonomously.

        Returns:
            (success, message) tuple.
        """
        # Ownership check
        owner = self._get_building_attr(building, "owner")
        if owner is not player:
            return False, "You do not own this building."

        building_type = self._get_building_attr(building, "building_type")
        if building_type is None:
            return False, "Cannot determine building type."

        try:
            building_def = self.registry.get_building(building_type)
        except KeyError:
            return False, f"Unknown building type: {building_type}"

        current_level = self._get_building_attr(building, "building_level", 1)
        if current_level >= MAX_BUILDING_LEVEL:
            return False, f"This building is already at maximum level ({MAX_BUILDING_LEVEL})."

        target_level = current_level + 1

        # Exponential cost
        upgrade_cost = self.get_upgrade_cost(building_def, target_level)
        err = self._validate_resources(player, upgrade_cost)
        if err:
            return False, err

        # Deduct resources
        player.deduct_resources(upgrade_cost)

        # Set upgrade timer on the building
        upgrade_time = self.get_upgrade_time(building_def, target_level)
        self._set_building_attr(building, "construction_progress", 0)
        self._set_building_attr(building, "construction_total", upgrade_time)
        self._set_building_attr(building, "under_construction", True)
        # Store the target level so completion knows what to set
        self._set_building_attr(building, "upgrade_target_level", target_level)

        # Set player into "building" activity state
        if hasattr(player, "db"):
            player.db.activity_state = "building"
            player.db.activity_target = building
            player.db.activity_progress = 0

        self.event_bus.publish(
            CONSTRUCTION_STARTED,
            player=player,
            building=building,
            tile=getattr(building, "location", None),
        )

        cost_str = ", ".join(f"{amt} {res}" for res, amt in upgrade_cost.items())
        return True, (
            f"Upgrading {building_def.name} to level {target_level} "
            f"(0/{upgrade_time}s, cost: {cost_str}). Stay on the tile to continue."
        )

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

        if not building_def.has_capability(UPGRADABLE):
            return False, "This building type cannot be upgraded."

        # 3. Level check
        current_level = building.building_level
        if current_level >= MAX_BUILDING_LEVEL:
            return False, "This building is already at maximum level (5)."

        target_level = current_level + 1

        # 4. Calculate upgrade cost: base_cost × 2^(target_level - 1)
        upgrade_cost = self.get_upgrade_cost(building_def, target_level)

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
    #  Construction timer & active-presence
    # ------------------------------------------------------------------ #

    def start_construction(
        self, player: Any, tile: Any, building_abbr: str,
        x: int | None = None, y: int | None = None,
    ) -> tuple[bool, str]:
        """Begin a timed construction requiring player active-presence.

        Returns:
            (success, message) tuple.
        """
        building_def, err = self._validate_construction(player, tile, building_abbr, x=x, y=y)
        if err:
            return False, err

        player.deduct_resources(building_def.cost)
        building = self._call_create_building(building_def, tile, player, x=x, y=y)

        # Initialise construction timer
        build_time = building_def.build_time_seconds
        self._set_building_attr(building, "construction_progress", 0)
        self._set_building_attr(building, "construction_total", build_time)
        self._set_building_attr(building, "under_construction", True)

        # Set player into "building" activity state
        if hasattr(player, "db"):
            player.db.activity_state = "building"
            player.db.activity_target = building
            player.db.activity_progress = 0

        self.event_bus.publish(
            CONSTRUCTION_STARTED,
            player=player,
            building=building,
            tile=tile,
        )

        return True, (
            f"Construction of {building_def.name} started "
            f"(0/{build_time}s). Stay on the tile to continue."
        )

    # Progress interval imported from world.constants

    def process_construction_tick(self, player: Any) -> bool:
        """Advance construction for a player in the ``"building"`` state.

        Called once per game tick for each online player.  Checks that
        the player is still in the ``"building"`` state and on the
        correct tile, then increments ``construction_progress``.  When
        progress reaches ``construction_total``, construction completes
        and the player returns to ``"idle"``.

        Returns:
            ``True`` if construction completed this tick.
        """
        if not hasattr(player, "db"):
            return False

        if getattr(player.db, "activity_state", "idle") != "building":
            return False

        building = getattr(player.db, "activity_target", None)
        if building is None:
            player.db.activity_state = "idle"
            return False

        # Verify player is still on the correct tile
        if not self._player_on_building_tile(player, building):
            return False

        # Increment progress
        progress = self._get_building_attr(building, "construction_progress", 0)
        total = self._get_building_attr(building, "construction_total", 0)
        progress += 1
        self._set_building_attr(building, "construction_progress", progress)

        if progress >= total:
            self._complete_construction(player, building)
            return True

        # Periodic progress update
        if player is not None and progress % CONSTRUCTION_PROGRESS_INTERVAL == 0:
            remaining = total - progress
            btype = self._get_building_attr(building, "building_type", "??")
            target_level = self._get_building_attr(building, "upgrade_target_level")
            self.notify(
                player, "building_progress", btype=btype, target_level=target_level,
                progress=progress, total=total, remaining=remaining,
            )

        return False

    def process_agent_construction(self, buildings: list) -> None:
        """Progress construction for buildings with assigned Engineer agents.

        Called once per game tick.  For each building that has an
        ``assigned_agent`` and a non-zero ``construction_total``,
        increments ``construction_progress``.  When complete, finalises
        the building and frees the agent.

        Args:
            buildings: Iterable of building objects to check.
        """
        for building in buildings:
            agent = self._get_building_attr(building, "assigned_agent", None)
            if agent is None:
                continue

            # Check agent is not incapacitated
            if getattr(getattr(agent, "db", None), "incapacitated", False):
                continue

            total = self._get_building_attr(building, "construction_total", 0)
            if total <= 0:
                continue

            progress = self._get_building_attr(
                building, "construction_progress", 0
            )
            if progress >= total:
                continue  # already complete

            progress += 1
            self._set_building_attr(building, "construction_progress", progress)

            if progress >= total:
                owner = self._get_building_attr(building, "owner", None)
                self._complete_construction(owner, building)

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

    def _validate_tile_empty(self, tile: Any, x: int | None = None, y: int | None = None) -> str | None:
        """Check tile has no existing building. Returns error or None.

        If *tile* is a PlanetRoom (has ``get_buildings_at``) and *x*/*y*
        are provided, queries the coordinate index.  Otherwise falls
        back to the legacy ``tile.building`` attribute check.
        """
        if hasattr(tile, "get_buildings_at") and x is not None and y is not None:
            if tile.get_buildings_at(x, y):
                return "This tile already contains a building."
            return None
        # Legacy fallback for OverworldRoom / test fakes
        building = getattr(tile, "building", None)
        if building is None:
            return None
        return "This tile already contains a building."

    def _validate_build_range(self, player: Any, tile: Any, x: int | None = None, y: int | None = None) -> str | None:
        """Check tile is within build range. Returns error or None.

        If *x*/*y* are provided they are used as the target coordinates
        directly (PlanetRoom path).  Otherwise falls back to extracting
        coordinates from *tile* via ``_get_coords``.
        """
        # --- player coordinates ---
        player_coords = self._get_coords(player)
        if player_coords is None:
            player_loc = getattr(player, "location", None)
            if player_loc is None:
                return None  # Can't validate without location
            player_coords = self._get_coords(player_loc)

        # --- target coordinates ---
        if x is not None and y is not None:
            tile_coords = (int(x), int(y))
        else:
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
        """Check player has sufficient resources. Returns error or None.

        On failure, returns the shared multi-line ``have/need`` breakdown (see
        :func:`world.utils.format_insufficient_resources`).
        """
        if player.has_resources(costs):
            return None
        from world.utils import format_insufficient_resources

        return format_insufficient_resources(player, costs)

    def _validate_rank_requirement(
        self, player: Any, building_def: BuildingDef
    ) -> str | None:
        """Check player level meets building's rank_requirement.

        rank_requirement is now a level requirement (1-60).
        Returns error message or None.
        """
        rank_req = getattr(building_def, "rank_requirement", 1)
        player_level = 1
        if hasattr(player, "db"):
            player_level = getattr(player.db, "level", None)
            if player_level is None:
                # Backward compat: fall back to rank_level
                player_level = getattr(player.db, "rank_level", 1) or 1
        if player_level < rank_req:
            return (
                f"Level {rank_req} required to build {building_def.name} "
                f"(current level: {player_level})."
            )
        return None

    def _validate_one_hq_per_planet(
        self, player: Any, building_def: BuildingDef, tile: Any
    ) -> str | None:
        """Enforce one HQ per player per planet.

        Returns error message or None.
        """
        if not building_def.has_capability(HEADQUARTERS):
            return None

        # Check if the player already has an HQ
        if self._player_has_hq(player):
            return "You can only have one Headquarters per planet."

        return None

    def _validate_extractor_terrain(
        self, tile: Any, building_def: BuildingDef,
        x: int | None = None, y: int | None = None,
    ) -> str | None:
        """Enforce Extractor placement on resource terrain.

        Queries the TerrainGenerator directly using the tile's
        coordinates, since room attributes may not be populated yet.

        If *x*/*y* are provided they take precedence (PlanetRoom path).
        Otherwise coordinates are read from the tile object.

        Returns error message or None.
        """
        if not building_def.has_capability(REQUIRES_RESOURCE_TERRAIN):
            return None

        # Get tile coordinates — prefer explicit params, then db, then properties
        if x is None:
            x = getattr(tile, "x", None)
            if x is None and hasattr(tile, "db"):
                x = getattr(tile.db, "coord_x", None) or getattr(tile.db, "x", None)
        if y is None:
            y = getattr(tile, "y", None)
            if y is None and hasattr(tile, "db"):
                y = getattr(tile.db, "coord_y", None) or getattr(tile.db, "y", None)

        # Try planet from tile tags or db
        planet = None
        if hasattr(tile, "tags"):
            try:
                planet = tile.tags.get(category="coord_planet", return_list=False)
            except Exception:
                pass
        if not planet and hasattr(tile, "db"):
            planet = getattr(tile.db, "planet", None)
        if not planet:
            planet = getattr(tile, "planet_name", None)

        # Ask the terrain provider directly — most reliable source. Prefer the
        # injected TerrainProvider port; fall back to the game_systems global
        # only when no provider was wired (legacy/test paths). A None terrain
        # means "no generator for this planet" → fall through to the room-based
        # checks below, exactly as the previous ``gen is None`` branch did. The
        # ``"unknown"`` sentinel (returned by a generator with no terrain
        # thresholds, e.g. a space-type planet with empty terrain_weights) is
        # likewise non-definitive and must fall through, not reject placement.
        if x is not None and y is not None and planet:
            provider = self._terrain_provider or self._legacy_terrain_provider()
            if provider is not None:
                try:
                    terrain, resource = provider.get_terrain_and_resource(
                        planet, int(x), int(y)
                    )
                    if terrain is not None and terrain != "unknown":
                        if resource:
                            return None
                        return "Extractor must be placed on terrain with a resource."
                except Exception:
                    pass

        # Fallback: check room's resource_node_data
        rn = getattr(tile, "resource_node", None)
        if rn and isinstance(rn, dict) and rn.get("resource_type"):
            return None

        if hasattr(tile, "db"):
            rn_data = getattr(tile.db, "resource_node_data", None)
            if rn_data and isinstance(rn_data, dict) and rn_data.get("resource_type"):
                return None

        # Fallback: direct attribute (test fakes)
        if getattr(tile, "resource_type", None):
            return None

        return "Extractor must be placed on terrain with a resource."

    # ------------------------------------------------------------------ #
    #  Repair
    # ------------------------------------------------------------------ #

    def get_repair_cost(self, building: Any) -> dict[str, int]:
        """Return the repair cost for an offline building (50% of base cost).

        Args:
            building: The building object to repair.

        Returns:
            Dict of resource_type -> amount (50% of base construction cost).
        """
        building_type = None
        if hasattr(building, "attributes"):
            building_type = building.attributes.get("building_type", default=None)
        elif hasattr(building, "db"):
            building_type = getattr(building.db, "building_type", None)

        if building_type is None:
            return {}

        try:
            building_def = self.registry.get_building(building_type)
        except KeyError:
            return {}

        return {
            resource: max(1, amount // 2)
            for resource, amount in building_def.cost.items()
        }

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _player_has_hq(self, player: Any) -> bool:
        """Check if the player owns a headquarters-capability building."""
        buildings = self._get_player_buildings(player)
        for b in buildings:
            btype = None
            if hasattr(b, "attributes"):
                btype = b.attributes.get("building_type", default=None)
            elif hasattr(b, "db"):
                btype = getattr(b.db, "building_type", None)
            if not btype:
                continue
            bdef = self.registry.resolve_building(btype)
            if bdef is not None and bdef.has_capability(HEADQUARTERS):
                return True
        return False

    def _get_player_buildings(self, player: Any) -> list:
        """Return all buildings owned by the player."""
        if hasattr(player, "get_buildings"):
            return player.get_buildings()
        return []

    def _get_coords(self, obj: Any) -> tuple[int, int] | None:
        """Extract (x, y) coordinates from an object."""
        from world.utils import get_coords
        return get_coords(obj)

    @staticmethod
    def _legacy_terrain_provider() -> Any | None:
        """Build a TerrainProvider from the game_systems global (fallback).

        Used only when no ``terrain_provider`` was injected — preserves the
        prior behavior of reading ``game_systems["_terrain_generators"]`` so
        Extractor validation still works in contexts that predate injection.
        Returns ``None`` if the global is unavailable.
        """
        try:
            from server.conf.game_init import game_systems
            from world.adapters.game_systems_terrain_provider import (
                GameSystemsTerrainProvider,
            )

            generators = game_systems.get("_terrain_generators", {})
            return GameSystemsTerrainProvider(generators)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Construction completion & tile helpers
    # ------------------------------------------------------------------ #

    def _complete_construction(self, player: Any, building: Any) -> None:
        """Finalise a completed construction or upgrade and reset player state."""
        # Mark construction as done
        self._set_building_attr(building, "construction_progress",
                                self._get_building_attr(building, "construction_total", 0))

        # Clear under-construction flag — building is now operational
        self._set_building_attr(building, "under_construction", False)

        building_type = self._get_building_attr(building, "building_type", "??")

        # If this was an upgrade, apply the new level and HP
        target_level = self._get_building_attr(building, "upgrade_target_level")
        if target_level is not None:
            old_level = self._get_building_attr(building, "building_level", 1)
            self._set_building_attr(building, "building_level", target_level)
            self._set_building_attr(building, "upgrade_target_level", None)

            # Increase max HP by 20% per level from base
            try:
                bdef = self.registry.get_building(building_type)
                new_max_hp = int(bdef.max_health * (1 + 0.2 * (target_level - 1)))
                self._set_building_attr(building, "hp_max", new_max_hp)
                self._set_building_attr(building, "hp", new_max_hp)
            except (KeyError, AttributeError):
                pass

            self.event_bus.publish(
                BUILDING_UPGRADED,
                player=player,
                building=building,
                old_level=old_level,
                new_level=target_level,
            )

            # Notify player
            self.notify(player, "building_complete", building_type=building_type, target_level=target_level)
        else:
            # New construction complete
            self.notify(player, "building_complete", building_type=building_type, target_level=None)

        # Clear construction timer
        self._set_building_attr(building, "construction_total", 0)

        # Reset player activity state to idle
        if player is not None and hasattr(player, "db"):
            if getattr(player.db, "activity_state", "") == "building":
                player.db.activity_state = "idle"
                player.db.activity_target = None
                player.db.activity_progress = 0

        # Publish completion event
        tile = getattr(building, "location", None)
        self.event_bus.publish(
            CONSTRUCTION_COMPLETED,
            player=player,
            building=building,
            tile=tile,
        )

    def _player_on_building_tile(self, player: Any, building: Any) -> bool:
        """Check if the player is on the same tile as the building."""
        from world.utils import player_at_building
        return player_at_building(player, building)

    @staticmethod
    def _get_building_attr(building: Any, key: str, default: Any = None) -> Any:
        """Read an attribute from a building object safely."""
        return _get_building_attr_shared(building, key, default)

    @staticmethod
    def _set_building_attr(building: Any, key: str, value: Any) -> None:
        """Write an attribute on a building object safely."""
        _set_building_attr_shared(building, key, value)
