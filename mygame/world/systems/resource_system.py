"""
Resource System for the RTS Combat Overworld game.

Handles manual resource gathering from terrain nodes, active-presence
harvesting, automated Extractor production from Harvester agents,
Extractor inventory management, and depleted node respawn cycles.

"""

from __future__ import annotations

from typing import Any

from world.constants import HARVESTABLE
from world.data_registry import DataRegistry
from world.definitions import BalanceConfig
from world.event_bus import RESOURCE_GATHERED, EventBus
from world.systems.base_system import BaseSystem
from world.utils import get_building_attr as _get_building_attr_shared


def _current_balance() -> BalanceConfig:
    """Live balance config for the class-level capacity/damage helpers.

    The ``@staticmethod`` helpers (which have no ``self.registry``) read the
    same hot-tunable values as the instance methods via the shared
    ``default_balance`` choke point, and still get ``BalanceConfig`` defaults in
    the fast unit-test suite (which registers no ``DataRegistry`` singleton).
    """
    from world.adapters.registry_definitions_provider import default_balance

    return default_balance()


class ResourceSystem(BaseSystem):
    """Manages resource gathering, building production, and node respawns.

    Args:
        registry: The DataRegistry holding terrain/building definitions
            and balance configuration.
        event_bus: The EventBus for publishing game events.
    """

    # Uses BaseSystem.__init__(registry, event_bus) unchanged.

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
    #  Active-presence harvesting
    # ------------------------------------------------------------------ #

    # Harvest cooldown, yield, and multiplier imported from world.constants

    def start_harvest(self, player: Any, tile: Any) -> tuple[bool, str]:
        """Begin active-presence harvesting on a resource tile.

        Sets the player's ``activity_state`` to ``"harvesting"`` and
        ``activity_target`` to the tile.  The player must remain on the
        tile for :meth:`process_harvest_tick` to yield resources.

        Supports both legacy OverworldRoom tiles (with resource_node_data)
        and PlanetRoom-based harvesting (where the tile IS a PlanetRoom
        and coordinates come from the player).

        Returns:
            (success, message) tuple.
        """
        # Block harvesting at incomplete buildings
        building = getattr(tile, "building", None)
        if building is not None:
            under_construction = self._get_building_attr(building, "under_construction")
            if under_construction:
                return False, "This building is still under construction."

        # Determine resource info — PlanetRoom path or legacy path
        resource_type = None
        is_depleted = False

        if hasattr(tile, "is_node_depleted") and hasattr(player, "db"):
            # PlanetRoom path: use player coordinates + TerrainGenerator
            px = getattr(player.db, "coord_x", None)
            py = getattr(player.db, "coord_y", None)
            if px is not None and py is not None:
                is_depleted = tile.is_node_depleted(px, py)
                if not is_depleted:
                    # Get resource type from TerrainGenerator
                    resource_type = self._get_terrain_resource(player, px, py)

        if resource_type is None:
            # Legacy OverworldRoom path
            node = self._get_resource_node(tile)
            if node is None:
                return False, "No resource node on this tile."
            resource_type = node.get("resource_type")
            if not resource_type:
                return False, "This tile has no harvestable resource."
            is_depleted = node.get("depleted", False)

        if is_depleted:
            return False, "This resource node is depleted."

        if not resource_type:
            return False, "No resource node on this tile."

        if not hasattr(player, "db"):
            return False, "Player has no attribute storage."

        player.db.activity_state = "harvesting"
        player.db.activity_target = tile
        player.db.activity_progress = 0

        # Tell the player what rate they'll get
        hx = getattr(player.db, "coord_x", None)
        hy = getattr(player.db, "coord_y", None)
        bal = self.registry.balance
        extractor = self._get_tile_extractor(tile, px=hx, py=hy)
        if extractor is not None:
            level = self._get_building_level(extractor)
            amount = int(bal.harvest_yield_per_action * bal.extractor_harvest_multiplier
                         * (1 + bal.extractor_level_bonus * (level - 1)))
            return True, (
                f"You begin harvesting {resource_type} at the Extractor. "
                f"({amount} per {bal.harvest_cooldown_ticks}s)"
            )

        return True, (
            f"You begin harvesting {resource_type}. "
            f"({bal.harvest_yield_per_action} per {bal.harvest_cooldown_ticks}s)"
        )

    def process_harvest_tick(self, player: Any) -> bool:
        """Advance active-presence harvesting for one game tick.

        Called once per tick for each online player.  If the player is
        in the ``"harvesting"`` state and still on the target tile,
        increments ``activity_progress``.  Every
        ``balance.harvest_cooldown_ticks`` ticks, yields
        ``balance.harvest_yield_per_action`` units of the tile's resource
        to the player and publishes a ``resource_gathered`` event.

        If the node becomes depleted, the player returns to ``"idle"``.

        Supports both legacy OverworldRoom tiles and PlanetRoom-based
        harvesting where coordinates come from the player.

        Returns:
            ``True`` if resources were yielded this tick.
        """
        if not hasattr(player, "db"):
            return False

        if getattr(player.db, "activity_state", "idle") != "harvesting":
            return False

        tile = getattr(player.db, "activity_target", None)
        if tile is None:
            player.db.activity_state = "idle"
            return False

        # Verify player is still on the target tile
        if not self._player_on_tile(player, tile):
            return False  # paused — don't reset, just skip

        # Determine resource info — PlanetRoom path or legacy path
        resource_type = None
        is_depleted = False
        px = getattr(player.db, "coord_x", None)
        py = getattr(player.db, "coord_y", None)

        if hasattr(tile, "is_node_depleted") and px is not None and py is not None:
            # PlanetRoom path
            is_depleted = tile.is_node_depleted(px, py)
            if not is_depleted:
                resource_type = self._get_terrain_resource(player, px, py)
        else:
            # Legacy OverworldRoom path
            node = self._get_resource_node(tile)
            if node is None or node.get("depleted", False):
                is_depleted = True
            else:
                resource_type = node.get("resource_type")

        if is_depleted or not resource_type:
            player.db.activity_state = "idle"
            player.db.activity_target = None
            player.db.activity_progress = 0
            return False

        # Increment progress
        progress = getattr(player.db, "activity_progress", 0) + 1
        player.db.activity_progress = progress

        # Yield resources on cooldown boundary
        bal = self.registry.balance
        if progress % bal.harvest_cooldown_ticks == 0:
            amount = bal.harvest_yield_per_action

            # Extractor bonus: if the tile has an Extractor building,
            # multiply yield by extractor_harvest_multiplier scaled by level.
            extractor = self._get_tile_extractor(tile, px=px, py=py)
            if extractor is not None:
                level = self._get_building_level(extractor)
                amount = int(amount * bal.extractor_harvest_multiplier
                             * (1 + bal.extractor_level_bonus * (level - 1)))
                # Alliance harvest_boost perk: an OWN multiplier applied ON TOP
                # of the base extractor factor (never reusing that key), read
                # LIVE from the harvesting player's alliance membership. 1.0
                # (no change) for a non-member or an alliance without the perk.
                amount = int(amount * self._alliance_harvest_multiplier(player))

            # Determine drop location and coordinates
            if hasattr(tile, "is_node_depleted") and px is not None and py is not None:
                # PlanetRoom path: spawn drop at player coordinates
                drop = self._spawn_resource_drop(
                    tile, resource_type, amount, x=px, y=py
                )
            else:
                # Legacy path: drop in OverworldRoom
                building = getattr(tile, "building", None)
                if building is not None and self._get_building_type(building) is not None:
                    drop_location = getattr(building, "location", None) or tile
                else:
                    drop_location = tile
                drop = self._spawn_resource_drop(drop_location, resource_type, amount)

            # A full tile refuses a NEW drop (returns None). Tell the player the
            # ground is full instead of the misleading "you harvested N" line,
            # and don't fire RESOURCE_GATHERED for a drop that never happened.
            # The resource is not lost — it simply wasn't generated this cycle.
            if drop is None:
                self.notify(player, "tile_full")
                return True

            self.notify(player, "harvest_drop", amount=amount, resource_type=resource_type)

            self.event_bus.publish(
                RESOURCE_GATHERED,
                player=player,
                resource_type=resource_type,
                amount=amount,
                tile=tile,
            )
            return True

        return False

    # ------------------------------------------------------------------ #
    #  Extractor inventory management
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_extractor_capacity(level: int) -> int:
        """Return the storage capacity for an Extractor at *level*."""
        bal = _current_balance()
        return bal.extractor_base_capacity + bal.extractor_capacity_per_level * (level - 1)

    @staticmethod
    def get_vault_capacity(level: int) -> int:
        """Return the storage capacity for a Vault at *level*."""
        bal = _current_balance()
        return bal.vault_base_capacity + bal.vault_capacity_per_level * (level - 1)

    @staticmethod
    def get_turret_damage(base_damage: int, level: int) -> float:
        """Return the turret damage at *level*.

        Formula: ``base × (1 + turret_level_bonus × (level - 1))``
        """
        return base_damage * (1 + _current_balance().turret_level_bonus * (level - 1))

    @staticmethod
    def get_harvester_production(base_rate: int, level: int) -> float:
        """Return the Harvester production rate at *level*.

        Formula: ``base_rate × (1 + extractor_level_bonus × (level - 1))``
        """
        return base_rate * (1 + _current_balance().extractor_level_bonus * (level - 1))

    @staticmethod
    def get_extractor_inventory(building: Any) -> dict[str, int]:
        """Return the resource inventory dict stored on an Extractor.

        The inventory is a ``dict[str, int]`` mapping resource type
        names to amounts, stored on ``building.db.resource_inventory``
        (or via the Evennia Attribute handler).
        """
        if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
            inv = building.attributes.get("resource_inventory", default=None)
            if inv is not None:
                return inv
        if hasattr(building, "db"):
            inv = getattr(building.db, "resource_inventory", None)
            if inv is not None:
                return inv
        return {}

    @staticmethod
    def _set_extractor_inventory(building: Any, inventory: dict[str, int]) -> None:
        """Write the resource inventory dict back to an Extractor."""
        if hasattr(building, "attributes") and hasattr(building.attributes, "add"):
            building.attributes.add("resource_inventory", inventory)
        elif hasattr(building, "db"):
            building.db.resource_inventory = inventory

    @classmethod
    def get_extractor_stored_amount(cls, building: Any) -> int:
        """Return the total number of resource units stored in an Extractor."""
        inv = cls.get_extractor_inventory(building)
        return sum(inv.values())

    @classmethod
    def add_to_extractor_inventory(
        cls, building: Any, resource_type: str, amount: int, level: int
    ) -> int:
        """Add resources to an Extractor's inventory, respecting capacity.

        Returns the amount actually added (may be less than *amount* if
        the Extractor is at or near capacity).
        """
        capacity = cls.get_extractor_capacity(level)
        inv = cls.get_extractor_inventory(building)
        current_total = sum(inv.values())
        space = max(0, capacity - current_total)
        actual = min(amount, space)
        if actual > 0:
            inv[resource_type] = inv.get(resource_type, 0) + actual
            cls._set_extractor_inventory(building, inv)
        return actual

    # ------------------------------------------------------------------ #
    #  Harvester agent / Extractor production
    # ------------------------------------------------------------------ #

    def process_extractor_production(self, buildings: list) -> None:
        """Produce resources for Extractors with assigned Harvester agents.

        Called once per game tick.  For each Extractor that has an
        ``assigned_agent`` whose role is ``"harvester"``, produces
        resources scaled by the Extractor's level and stores them in
        the Extractor's local inventory.

        Production formula:
            ``base_rate × (1 + 0.25 × (level - 1))``

        where ``base_rate`` is ``balance.gather_amount``.

        Production pauses when the Extractor's inventory is full or the
        agent is incapacitated.

        Args:
            buildings: Iterable of building objects to check.
        """
        base_rate = self.registry.balance.gather_amount

        for building in buildings:
            # Must have an assigned agent
            agent = self._get_building_attr(building, "assigned_agent")
            if agent is None:
                continue

            # Agent must be a harvester and not incapacitated
            if getattr(getattr(agent, "db", None), "role", "") != "harvester":
                continue
            if getattr(getattr(agent, "db", None), "incapacitated", False):
                continue

            # Must be a harvestable building (Extractor)
            building_type = self._get_building_type(building)
            if not building_type:
                continue
            try:
                building_def = self.registry.get_building(building_type)
            except KeyError:
                continue
            if not building_def.has_capability(HARVESTABLE):
                continue

            resource_type = building_def.produces
            if not resource_type:
                # Extractor: resolve from building attribute or terrain tile
                resource_type = self._resolve_extractor_resource(building)
            if not resource_type:
                continue

            # Skip offline buildings
            if getattr(building, "is_offline", False):
                continue

            # Calculate production amount scaled by level
            level = self._get_building_level(building)
            production = base_rate * (
                1 + self.registry.balance.extractor_level_bonus * (level - 1)
            )
            # Round to nearest integer (at least 1 if base_rate > 0)
            production_int = max(1, int(production)) if base_rate > 0 else 0

            if production_int <= 0:
                continue

            # Drop resources as objects on the building's tile. Pass the
            # building's coords so the drop merges with an existing pile there
            # AND is subject to the tile item-capacity cap (an Extractor tile
            # holds room_capacity_per_storage_level x level). A full tile returns
            # None: production is skipped this cycle (nothing generated, nothing
            # lost) — the RESOURCE_GATHERED event only fires on an actual drop.
            drop_location = getattr(building, "location", building)
            bx = getattr(getattr(building, "db", None), "coord_x", None)
            by = getattr(getattr(building, "db", None), "coord_y", None)
            drop = self._spawn_resource_drop(
                drop_location, resource_type, production_int, x=bx, y=by
            )
            if drop is None:
                # Tile at capacity — stop generating here until it's cleared.
                continue

            if production_int > 0:
                owner = self._get_building_attr(building, "owner")
                self.event_bus.publish(
                    RESOURCE_GATHERED,
                    player=owner,
                    resource_type=resource_type,
                    amount=production_int,
                    tile=building,
                )

    # ------------------------------------------------------------------ #
    #  Resource node respawn
    # ------------------------------------------------------------------ #

    def process_respawns(self, tiles: list) -> None:
        """Process respawn counters for depleted resource nodes.

        Supports both PlanetRoom objects (with get_depleted_nodes/
        clear_node_depletion) and legacy OverworldRoom tiles (with
        resource_node_data attribute).

        For PlanetRoom:
            - Iterate get_depleted_nodes(), decrement counters
            - Call clear_node_depletion when counter reaches 0

        For legacy OverworldRoom tiles:
            - Decrement respawn_counter on resource_node_data
            - Set depleted=False when counter reaches 0

        Args:
            tiles: List of PlanetRoom or OverworldRoom tiles to process.
        """
        for tile in tiles:
            # PlanetRoom path: iterate depletion dict
            if hasattr(tile, "get_depleted_nodes") and hasattr(tile, "clear_node_depletion"):
                depleted = tile.get_depleted_nodes()
                if not depleted:
                    continue
                # Collect keys to clear (can't modify dict during iteration)
                to_clear = []
                for key, entry in list(depleted.items()):
                    counter = entry.get("respawn_counter", 0)
                    counter -= 1
                    if counter <= 0:
                        to_clear.append(key)
                    else:
                        entry["respawn_counter"] = counter
                # Update the dict on the room
                for key in to_clear:
                    depleted.pop(key, None)
                tile.db.depleted_nodes = depleted
                # Also call clear_node_depletion for each cleared key
                for key in to_clear:
                    parts = key.split(",")
                    if len(parts) == 2:
                        try:
                            tile.clear_node_depletion(int(parts[0]), int(parts[1]))
                        except (ValueError, TypeError):
                            pass
                continue

            # Legacy OverworldRoom path
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
        from world.utils import get_building_type
        return get_building_type(building)

    @staticmethod
    def _get_building_level(building: Any) -> int:
        """Read the building level from a building."""
        from world.utils import get_building_level
        return get_building_level(building)

    @staticmethod
    def _alliance_harvest_multiplier(player: Any) -> float:
        """Return the alliance harvest_boost multiplier for *player* (1.0 if none).

        Read LIVE from the player's alliance membership so leaving the alliance
        removes the boost immediately. ``1.0`` (no change) when there is no
        AllianceSystem, the player is not a member, or the alliance has no active
        harvest_boost perk. Guarded so a lookup never breaks harvesting.
        """
        try:
            from world.utils import get_system
            system = get_system(player, "alliance_system")
            if system is None:
                return 1.0
            return float(system.perk_multiplier(player, "harvest_boost"))
        except Exception:  # noqa: BLE001 - a perk lookup never breaks harvest
            return 1.0

    @staticmethod
    def _player_on_tile(player: Any, tile: Any) -> bool:
        """Return ``True`` if the player is on the given tile.

        Checks Evennia ``location`` first, then falls back to
        coordinate comparison.
        """
        # Direct location check (Evennia rooms)
        if hasattr(player, "location") and player.location is tile:
            return True

        # Coordinate-based check
        px = getattr(getattr(player, "db", None), "coord_x", None)
        py = getattr(getattr(player, "db", None), "coord_y", None)
        tx = getattr(tile, "x", getattr(getattr(tile, "db", None), "coord_x", None))
        ty = getattr(tile, "y", getattr(getattr(tile, "db", None), "coord_y", None))

        if px is not None and py is not None and tx is not None and ty is not None:
            return px == tx and py == ty

        return False

    def _get_terrain_resource(self, player: Any, x: int, y: int) -> str | None:
        """Look up the resource type at (x, y) from TerrainGenerator.

        Uses the player's coord_planet to find the right generator.
        Returns None if no resource exists at the coordinate.
        """
        planet = getattr(getattr(player, "db", None), "coord_planet", None)
        if not planet:
            return None
        try:
            # Try to get terrain generators from game_systems
            from server.conf.game_init import game_systems
            generators = game_systems.get("_terrain_generators", {})
            gen = generators.get(planet)
            if gen:
                _terrain_type, resource_type = gen.get_terrain_and_resource(x, y)
                return resource_type
        except (ImportError, AttributeError):
            pass
        return None

    def _get_tile_extractor(self, tile: Any, px: int = None, py: int = None) -> Any | None:
        """Return the Extractor building on *tile* at player coords, or None.

        For PlanetRoom tiles, queries get_buildings_at(px, py).
        For legacy tiles, checks the tile's ``building`` attribute.
        Verifies it's a harvestable building (Extractor) that is
        not offline and not under construction.
        """
        building = None

        # PlanetRoom path: query by coordinates
        if px is not None and py is not None and hasattr(tile, "get_buildings_at"):
            buildings = tile.get_buildings_at(int(px), int(py))
            if buildings:
                building = buildings[0]
        else:
            # Legacy path
            building = getattr(tile, "building", None)

        if building is None:
            return None

        btype = None
        if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
            btype = building.attributes.get("building_type", default=None)
        elif hasattr(building, "db"):
            btype = getattr(building.db, "building_type", None)

        bdef = self.registry.resolve_building(btype) if btype else None
        if bdef is None or not bdef.has_capability(HARVESTABLE):
            return None

        if getattr(building, "is_offline", False):
            return None

        # Not operational while under construction
        under_construction = False
        if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
            under_construction = building.attributes.get("under_construction", default=False)
        elif hasattr(building, "db"):
            under_construction = getattr(building.db, "under_construction", False)
        if under_construction:
            return None

        return building

    @staticmethod
    def _resolve_extractor_resource(building: Any) -> str | None:
        """Determine the resource type for an Extractor.

        Checks the building's stored ``resource_type`` attribute first,
        then falls back to reading the terrain tile's resource node.
        Used by both ``process_extractor_production`` and
        ``HarvesterScript``.
        """
        # Explicit attribute on the building
        if hasattr(building, "attributes") and hasattr(building.attributes, "get"):
            rt = building.attributes.get("resource_type", default=None)
            if rt:
                return rt
        if hasattr(building, "db"):
            rt = getattr(building.db, "resource_type", None)
            if rt:
                return rt

        # Fall back to the terrain tile the building sits on
        tile = getattr(building, "location", None)
        if tile is None:
            return None

        # Try resource_node_data dict
        node = None
        if hasattr(tile, "attributes") and hasattr(tile.attributes, "get"):
            node = tile.attributes.get("resource_node_data", default=None)
        if isinstance(node, dict):
            return node.get("resource_type")

        # Try direct terrain attribute
        return getattr(tile, "resource_type", None)

    @staticmethod
    def _get_building_attr(building: Any, key: str, default: Any = None) -> Any:
        """Read an attribute from a building object."""
        return _get_building_attr_shared(building, key, default)

    @staticmethod
    def _spawn_resource_drop(location: Any, resource_type: str, amount: int,
                             x: int | None = None, y: int | None = None) -> Any:
        """Spawn or merge a ResourceDrop object at *location*.

        When x/y are provided, passes them through to spawn_resource_drop
        for coordinate-aware merge and placement in PlanetRoom.

        In test environments without Evennia, falls back to the old
        dict-based resource_inventory pattern.
        """
        if amount <= 0:
            return None
        try:
            from typeclasses.objects import spawn_resource_drop
            # None here means the tile is at its item-capacity cap (a NEW drop
            # was refused); a real object means it was placed/merged. Callers
            # rely on this to distinguish "generated" from "tile full".
            return spawn_resource_drop(location, resource_type, amount, x=x, y=y)
        except Exception:
            # Fallback for test environments without Evennia: use dict inventory.
            # Return a truthy sentinel (NOT None) so callers that treat None as
            # "tile full" don't mis-read a successful fallback as a full tile.
            if hasattr(location, "attributes") and hasattr(location.attributes, "add"):
                inv = location.attributes.get("resource_inventory", default=None) or {}
                inv[resource_type] = inv.get(resource_type, 0) + amount
                location.attributes.add("resource_inventory", inv)
            elif hasattr(location, "db"):
                inv = getattr(location.db, "resource_inventory", None) or {}
                inv[resource_type] = inv.get(resource_type, 0) + amount
                location.db.resource_inventory = inv
            return True

    @staticmethod
    def spawn_resource_drop(location: Any, resource_type: str, amount: int,
                            x: int | None = None, y: int | None = None) -> Any:
        """Public: spawn or merge a ResourceDrop at *location* (optionally at x/y).

        Stable entry point for callers outside the resource system — e.g. the
        PvE base-elimination loot drop — so they don't reach into the private
        ``_spawn_resource_drop``. Delegates to it.
        """
        return ResourceSystem._spawn_resource_drop(
            location, resource_type, amount, x=x, y=y
        )

    # ------------------------------------------------------------------ #
    #  Tile inventory (resources on the ground)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_tile_inventory(tile: Any) -> dict[str, int]:
        """Read the resource inventory dict from a tile (ground drops)."""
        if hasattr(tile, "attributes") and hasattr(tile.attributes, "get"):
            inv = tile.attributes.get("resource_inventory", default=None)
            if inv is not None:
                return inv
        if hasattr(tile, "db"):
            inv = getattr(tile.db, "resource_inventory", None)
            if inv is not None:
                return inv
        return {}

    @staticmethod
    def _set_tile_inventory(tile: Any, inventory: dict[str, int]) -> None:
        """Write the resource inventory dict to a tile."""
        if hasattr(tile, "attributes") and hasattr(tile.attributes, "add"):
            tile.attributes.add("resource_inventory", inventory)
        elif hasattr(tile, "db"):
            tile.db.resource_inventory = inventory

    @staticmethod
    def get_tile_inventory(tile: Any) -> dict[str, int]:
        """Public: return the resource inventory on a tile (ground drops)."""
        return ResourceSystem._get_tile_inventory(tile)

    @staticmethod
    def set_tile_inventory(tile: Any, inventory: dict[str, int]) -> None:
        """Public: write the resource inventory on a tile."""
        ResourceSystem._set_tile_inventory(tile, inventory)
