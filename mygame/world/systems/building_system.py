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
            lambda: self._validate_shield_generator_cap(player, building_def, tile, x=x, y=y),
            lambda: self._validate_rank_requirement(player, building_def),
            lambda: self._validate_deed_requirement(player, building_def),
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
                building = self._create_building_func(building_def, tile, owner, x=x, y=y)
            except TypeError:
                # Factory doesn't accept x/y — call without and set coords after
                building = self._create_building_func(building_def, tile, owner)
                # Stamp coords + register in the coordinate index
                # (at_object_receive missed it during create_object).
                from world.utils import place_on_tile
                place_on_tile(building, tile, x, y)
        else:
            building = self._create_building_func(building_def, tile, owner)
        self._apply_building_hp_tech_bonus(owner, building)
        return building

    def _apply_building_hp_tech_bonus(self, owner: Any, building: Any) -> None:
        """Raise a new/upgraded building's hp_max by the owner's tech bonus.

        The ``building_hp`` consumer read point (R13.3): a flat addition from
        ``db.tech_bonuses`` on top of the level-scaled hp_max the factory or
        upgrade path just set. Applied at creation and upgrade completion —
        existing buildings gain it on their next upgrade, matching the
        "new computations read the bonus" model (no retroactive sweep).
        """
        from world.utils import get_tech_bonus
        bonus = int(get_tech_bonus(owner, "building_hp"))
        if bonus <= 0 or building is None:
            return
        hp_max = int(self._get_building_attr(building, "hp_max", 0) or 0)
        hp = int(self._get_building_attr(building, "hp", 0) or 0)
        if hp_max <= 0:
            return
        self._set_building_attr(building, "hp_max", hp_max + bonus)
        # A fresh build/upgrade is at full HP; keep it full with the bonus.
        if hp >= hp_max:
            self._set_building_attr(building, "hp", hp_max + bonus)

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

    def _validate_upgrade(
        self, player: Any, building: Any
    ) -> tuple[bool, Any, int, dict, str]:
        """Shared validation for both upgrade paths (timed + instant).

        Runs the ownership / building-type / max-level / cost / resource checks
        that ``start_upgrade`` and ``upgrade`` used to duplicate (with drifting
        details — one read ``getattr(building, "owner")`` and hardcoded the
        max-level string, the other used ``_get_building_attr``). Both now share
        this single source, always using ``_get_building_attr`` and the
        ``MAX_BUILDING_LEVEL`` constant.

        Returns ``(ok, building_def, target_level, upgrade_cost, error)``. When
        ``ok`` is False, *error* holds the player-facing reason and the other
        fields are unset (``None``/``0``/``{}``). Resources are NOT deducted here
        — the caller deducts after any path-specific checks.
        """
        owner = self._get_building_attr(building, "owner")
        if owner is not player:
            return False, None, 0, {}, "You do not own this building."

        building_type = self._get_building_attr(building, "building_type")
        if building_type is None:
            return False, None, 0, {}, "Cannot determine building type."

        try:
            building_def = self.registry.get_building(building_type)
        except KeyError:
            return False, None, 0, {}, f"Unknown building type: {building_type}"

        current_level = self._get_building_attr(building, "building_level", 1)
        if current_level >= MAX_BUILDING_LEVEL:
            return False, None, 0, {}, (
                f"This building is already at maximum level "
                f"({MAX_BUILDING_LEVEL})."
            )

        target_level = current_level + 1
        upgrade_cost = self.get_upgrade_cost(building_def, target_level)
        err = self._validate_resources(player, upgrade_cost)
        if err:
            return False, None, 0, {}, err

        return True, building_def, target_level, upgrade_cost, ""

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
        ok, building_def, target_level, upgrade_cost, err = (
            self._validate_upgrade(player, building)
        )
        if not ok:
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

        from world.utils import format_cost_summary
        cost_str = format_cost_summary(upgrade_cost)
        return True, (
            f"Upgrading {building_def.name} to level {target_level} "
            f"(0/{upgrade_time}s, cost: {cost_str}). Stay on the tile to continue."
        )

    def upgrade(self, player: Any, building: Any) -> tuple[bool, str]:
        """Upgrade an upgradable building to the next level, INSTANTLY.

        The timeless variant of :meth:`start_upgrade` (no construction timer —
        the level bumps immediately and ``BUILDING_UPGRADED`` fires). Shares the
        ownership / max-level / cost / resource validation via
        :meth:`_validate_upgrade`; adds one path-specific check: the building
        must declare the ``UPGRADABLE`` capability.

        Returns:
            (success, message) tuple.
        """
        # Path-specific: only UPGRADABLE-capability buildings use the instant
        # path (start_upgrade has no such restriction). Resolve the type the
        # same way _validate_upgrade does, so the capability check is consistent.
        building_type = self._get_building_attr(building, "building_type")
        if building_type is not None:
            try:
                bdef = self.registry.get_building(building_type)
                if not bdef.has_capability(UPGRADABLE):
                    return False, "This building type cannot be upgraded."
            except KeyError:
                pass  # unknown type — _validate_upgrade returns the error below

        ok, building_def, target_level, upgrade_cost, err = (
            self._validate_upgrade(player, building)
        )
        if not ok:
            return False, err

        # All checks passed — deduct resources and upgrade instantly.
        player.deduct_resources(upgrade_cost)

        old_level = target_level - 1
        self._set_building_attr(building, "building_level", target_level)

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

    def _validate_deed_requirement(
        self, player: Any, building_def: BuildingDef
    ) -> str | None:
        """Check the player holds the building's deed gate (R9/D9).

        ``unlock_deed`` names a deed the player must have earned at least
        ``unlock_deed_count`` times (``db.deeds`` is a deed-id → count dict;
        boolean deeds are count >= 1). No deed gate → no check.
        Returns error message or None.
        """
        unlock_deed = getattr(building_def, "unlock_deed", None)
        if not unlock_deed:
            return None
        required = getattr(building_def, "unlock_deed_count", 1) or 1
        deeds = getattr(getattr(player, "db", None), "deeds", None) or {}
        have = deeds.get(unlock_deed, 0) if isinstance(deeds, dict) else 0
        if have < required:
            from world.constants import DEED_DESCRIPTIONS
            desc = DEED_DESCRIPTIONS.get(unlock_deed, unlock_deed)
            if required > 1:
                return (
                    f"Requires: {desc} ×{required} ({have}/{required}) "
                    f"to build {building_def.name}."
                )
            return f"Requires: {desc} to build {building_def.name}."
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

    def _validate_shield_generator_cap(
        self, player: Any, building_def: BuildingDef, tile: Any,
        x: int | None = None, y: int | None = None,
    ) -> str | None:
        """Enforce the per-player, per-planet Shield Generator cap.

        A player may hold at most ``MAX_SHIELD_GENERATORS_PER_PLANET`` buildings
        with the ``shield_generator`` capability on any one planet (a future
        tech may raise this). Counts existing generators on the SAME planet as
        the target tile; generators on other planets don't count. Returns an
        error message or None.
        """
        from world.constants import SHIELD_GENERATOR, MAX_SHIELD_GENERATORS_PER_PLANET
        if not building_def.has_capability(SHIELD_GENERATOR):
            return None

        planet = self._tile_planet(tile, x=x, y=y)
        existing = 0
        for b in self._get_player_buildings(player):
            if not self._building_has_capability(b, SHIELD_GENERATOR):
                continue
            # Planet-scope the count when we can resolve both planets; if the
            # target planet is unknown, count all (fail safe — never over-cap).
            if planet is not None:
                b_planet = self._get_building_attr(b, "coord_planet", None)
                if b_planet is not None and b_planet != planet:
                    continue
            existing += 1
        if existing >= MAX_SHIELD_GENERATORS_PER_PLANET:
            return (
                f"You can only have {MAX_SHIELD_GENERATORS_PER_PLANET} Shield "
                f"Generators per planet (you have {existing})."
            )
        return None

    def _building_has_capability(self, building: Any, capability: str) -> bool:
        """Value-based capability check for a live building (hermetic registry)."""
        from world.utils import building_has_capability
        return building_has_capability(building, capability, provider=self.registry)

    def _tile_planet(
        self, tile: Any, x: int | None = None, y: int | None = None
    ) -> str | None:
        """Best-effort resolve the planet key for a target tile."""
        planet = None
        if hasattr(tile, "tags"):
            try:
                planet = tile.tags.get(category="coord_planet", return_list=False)
            except Exception:  # noqa: BLE001
                planet = None
        if not planet and hasattr(tile, "db"):
            planet = getattr(tile.db, "planet", None) or getattr(tile.db, "coord_planet", None)
        if not planet:
            planet = getattr(tile, "planet_name", None)
        return planet

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

    def get_building_investment(self, building_def: BuildingDef, level: int) -> dict[str, int]:
        """Return the CUMULATIVE resource investment in a *level* building.

        The base construction cost PLUS every upgrade cost up to *level*: a
        level-5 building's investment is its build cost + the L2, L3, L4 and L5
        upgrade costs summed per resource. Level 1 (or below) is just the base
        build cost. This is the basis the tick-based repair charges against —
        repairing a heavily-invested, upgraded building costs proportionally
        more than a fresh one.
        """
        total: dict[str, float] = {res: float(amt) for res, amt in building_def.cost.items()}
        for lvl in range(2, max(1, int(level or 1)) + 1):
            for res, amt in self.get_upgrade_cost(building_def, lvl).items():
                total[res] = total.get(res, 0.0) + float(amt)
        return {res: int(round(amt)) for res, amt in total.items()}

    def get_repair_cost_per_tick(self, building: Any) -> dict[str, int]:
        """Return the per-tick resource cost of repairing *building*.

        One repair tick restores ``repair_hp_percent_per_tick``% of max HP and
        costs the SAME percent of the building's cumulative investment (see
        :meth:`get_building_investment`). Each resource line is rounded UP so a
        tick of any resource in the investment costs at least 1 of it — the
        pay-as-you-go charge, so an interrupted repair only bills for the HP it
        actually restored. Returns ``{}`` for an unknown type or a building
        whose investment is empty.

        Args:
            building: The building object being repaired.

        Returns:
            Dict of resource_type -> amount charged this tick.
        """
        import math

        building_type = self._get_building_attr(building, "building_type")
        if building_type is None:
            return {}
        try:
            building_def = self.registry.get_building(building_type)
        except KeyError:
            return {}

        level = int(self._get_building_attr(building, "building_level", 1) or 1)
        investment = self.get_building_investment(building_def, level)
        percent = float(getattr(self.registry.balance, "repair_hp_percent_per_tick", 5.0))
        if percent <= 0:
            return {}
        fraction = percent / 100.0
        return {
            resource: max(1, math.ceil(amount * fraction))
            for resource, amount in investment.items()
            if amount > 0
        }

    def repair(self, player: Any, building: Any) -> tuple[bool, str]:
        """Begin a tick-based repair of a damaged building the player owns.

        Buildings do not passively regenerate (unlike players/agents), so this
        is the only way to restore building HP. Repair is active-presence, just
        like construction: this validates and starts it, then each tick (while
        the player stays on the tile, or an assigned Engineer works it) restores
        ``repair_hp_percent_per_tick``% of max HP and charges the matching
        per-tick cost via :meth:`process_repair_tick`.

            1. Owner check.
            2. Reject if under construction, unrepairable, or already full HP.
            3. Require at least the FIRST tick's cost up front (so a repair with
               zero resources fails immediately with the shared have/need
               breakdown rather than silently starting and stalling).
            4. Put the player into the ``"repairing"`` activity state.

        Returns:
            (success, message) tuple.
        """
        from world.utils import is_owner

        owner = self._get_building_attr(building, "owner")
        if not is_owner(player, owner):
            return False, "You do not own this building."

        building_type = self._get_building_attr(building, "building_type")
        if building_type is None:
            return False, "Cannot determine building type."

        # Don't repair a building still under construction — finish it instead.
        if self._get_building_attr(building, "under_construction", False):
            return False, "This building is still under construction."

        hp = int(self._get_building_attr(building, "hp", 0) or 0)
        hp_max = int(self._get_building_attr(building, "hp_max", 0) or 0)
        if hp_max <= 0:
            return False, "This building cannot be repaired."
        if hp >= hp_max:
            return False, "This building is already at full health."

        # Must at least afford the first tick, else fail up front.
        per_tick = self.get_repair_cost_per_tick(building)
        err = self._validate_resources(player, per_tick)
        if err:
            return False, err

        # Enter the active-presence repair state (mirrors construction).
        if hasattr(player, "db"):
            player.db.activity_state = "repairing"
            player.db.activity_target = building

        from world.utils import format_cost_summary
        name = self._building_name(building_type)
        percent = float(getattr(self.registry.balance, "repair_hp_percent_per_tick", 5.0))
        cost_str = format_cost_summary(per_tick) or "nothing"
        return True, (
            f"Repairing {name} ({hp}/{hp_max} HP). Restores {percent:.0f}% HP "
            f"per tick at {cost_str}/tick — stay on the tile or assign an "
            f"Engineer to continue."
        )

    def process_repair_tick(self, player: Any) -> bool:
        """Advance an active-presence repair for a player in ``"repairing"``.

        Called once per tick per online player (mirrors
        :meth:`process_construction_tick`). Verifies the player is still
        repairing and on the building's tile, then applies one repair step,
        charging *player*. Leaves the ``"repairing"`` state when the building
        reaches full HP or the player can't afford the next tick.

        Returns:
            ``True`` when the repair finished (full HP) this tick.
        """
        if not hasattr(player, "db"):
            return False
        if getattr(player.db, "activity_state", "idle") != "repairing":
            return False

        building = getattr(player.db, "activity_target", None)
        if building is None:
            player.db.activity_state = "idle"
            return False
        if not self._player_on_building_tile(player, building):
            return False

        done, reason = self.apply_repair_step(building, player)
        if done or reason == "insufficient":
            player.db.activity_state = "idle"
            player.db.activity_target = None
        return done

    def apply_repair_step(self, building: Any, payer: Any) -> tuple[bool, str]:
        """Apply ONE repair tick to *building*, charged to *payer*.

        Restores ``repair_hp_percent_per_tick``% of max HP (at least 1 HP) and
        deducts the matching per-tick cost (:meth:`get_repair_cost_per_tick`)
        from *payer*. Pay-as-you-go: the charge lands per tick, so an
        interrupted repair only bills for the HP actually restored. A building
        knocked offline (HP 0) comes back online as soon as it starts healing.
        Shared by the player active-presence path (:meth:`process_repair_tick`)
        and the Engineer script.

        Returns:
            ``(finished, reason)`` — ``finished`` True at full HP; ``reason`` is
            ``"full"``, ``"repaired"`` (progressed this tick), ``"insufficient"``
            (payer can't afford this tick — nothing applied), or ``"noop"``
            (nothing to do / unrepairable).
        """
        hp = int(self._get_building_attr(building, "hp", 0) or 0)
        hp_max = int(self._get_building_attr(building, "hp_max", 0) or 0)
        if hp_max <= 0:
            return False, "noop"
        if hp >= hp_max:
            return True, "full"

        per_tick = self.get_repair_cost_per_tick(building)
        if per_tick and payer is not None and not payer.has_resources(per_tick):
            return False, "insufficient"
        if per_tick and payer is not None:
            payer.deduct_resources(per_tick)

        percent = float(getattr(self.registry.balance, "repair_hp_percent_per_tick", 5.0))
        step = max(1, int(round(hp_max * percent / 100.0)))
        new_hp = min(hp_max, hp + step)
        self._set_building_attr(building, "hp", new_hp)

        # A building that hit 0 HP was set offline; restore it once healing.
        if bool(self._get_building_attr(building, "offline", False)):
            self._set_building_attr(building, "offline", False)

        if new_hp >= hp_max:
            return True, "full"
        return False, "repaired"

    def _building_name(self, building_type: str) -> str:
        """Return the display name for a building type, or the type itself."""
        try:
            return self.registry.get_building(building_type).name
        except KeyError:
            return building_type

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _player_has_hq(self, player: Any) -> bool:
        """Check if the player owns a headquarters-capability building.

        Shares the ``get_buildings`` + ``HEADQUARTERS`` enumeration with
        :func:`world.utils.owner_has_active_hq` via ``_owner_hq_buildings``
        (Req 12.5). Semantics are unchanged from the previous inline loop: NOT
        planet-scoped (an HQ anywhere counts, for the one-HQ build gate) and it
        COUNTS an HQ still under construction — unlike ``owner_has_active_hq``,
        which excludes a half-built HQ. Passes ``self.registry`` as the
        capability provider so the check stays hermetic in tests.
        """
        from world.utils import _owner_hq_buildings
        return any(
            _owner_hq_buildings(player, planet=None, provider=self.registry)
        )

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
                # Owner's researched building_hp tech (R13.3) tops up the
                # recomputed hp_max — the upgrade path recalculates from the
                # base def, so the bonus must be re-applied here.
                owner = self._get_building_attr(building, "owner") or player
                self._apply_building_hp_tech_bonus(owner, building)
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

            # Completing a NEW HQ reactivates a base that was inert (its previous
            # HQ was destroyed). Reactivation only makes sense when an HQ itself
            # finishes — a non-HQ build never restores base operations — so this
            # needs no stored "was deactivated" flag: the event that flips the
            # base back to active IS a fresh HQ completing. (An HQ *upgrade*
            # takes the target_level branch above; the base was already active.)
            #
            # Only fire for a genuine REBUILD — a base that still has other
            # buildings whose HQ was destroyed — not a brand-new player's
            # first-ever HQ (which owns no other structures yet; "rebuilt" would
            # read wrong). Distinguished cheaply by whether the player owns any
            # building besides this HQ.
            try:
                bdef = self.registry.resolve_building(building_type)
            except Exception:
                bdef = None
            if bdef is not None and bdef.has_capability(HEADQUARTERS):
                # Exclude the just-built HQ itself when checking for "other"
                # buildings. Match by .id (the codebase's equality convention,
                # robust across an idmapper flush that could hand back a distinct
                # same-PK instance); fall back to identity when there is no id
                # (test doubles).
                built_id = getattr(building, "id", None)

                def _is_the_new_hq(b):
                    bid = getattr(b, "id", None)
                    if built_id is not None and bid is not None:
                        return bid == built_id
                    return b is building

                others = [
                    b for b in self._get_player_buildings(player)
                    if not _is_the_new_hq(b)
                ]
                if others:
                    self.notify(player, "base_reactivated")

        # Clear construction timer
        self._set_building_attr(building, "construction_total", 0)

        # Reset player activity state to idle
        if player is not None and hasattr(player, "db"):
            if getattr(player.db, "activity_state", "") == "building":
                player.db.activity_state = "idle"
                player.db.activity_target = None
                player.db.activity_progress = 0

        # Economy XP award (R1.1, R1.2 — both player-present and engineer paths).
        # Distinguish: an upgrade has upgrade_target_level set to None (already
        # consumed above), but building_level > 1 after the upgrade branch ran.
        # A new build stays at level 1 with no prior upgrade_target_level.
        # Simplest: track was_upgrade as a local in the target_level branch above.
        # Since target_level was already resolved earlier in this method, use it:
        # this code runs after the branch where target_level was cleared to None
        # on the building — so re-derive from the building_level: >1 means upgrade.
        bl = self._get_building_attr(building, "building_level", 1) or 1
        if bl > 1:
            self._award_economy_xp(player, "upgrade_complete")
        else:
            self._award_economy_xp(player, "build_complete")

        # Publish completion event
        tile = getattr(building, "location", None)
        self.event_bus.publish(
            CONSTRUCTION_COMPLETED,
            player=player,
            building=building,
            tile=tile,
        )

    def _award_economy_xp(self, player: Any, reason: str,
                           amount: int | None = None) -> None:
        """Award economy XP to *player* via RankSystem (R1).

        Looks up the amount from ``balance.xp_{reason}`` if not supplied, then
        routes through the shared :func:`world.utils.award_player_xp` choke
        point (silent no-op on None player / zero amount / no RankSystem).
        """
        if amount is None:
            amount = getattr(self.registry.balance, f"xp_{reason}", 0) or 0
        from world.utils import award_player_xp
        award_player_xp(player, amount, reason=reason)

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
