"""
Tile Resolver — single entry point for resolving (x, y, planet) to rooms.

All game systems use TileResolver instead of XYZRoom.objects.get_xyz.
Lookup order: cache → database (tag-based query) → create new room.

Requirements: 2.1–2.10, 9.1–9.6
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from world.coordinate.planet_registry import PlanetRegistry
    from world.coordinate.room_cache import RoomCache
    from world.coordinate.terrain_generator import TerrainGenerator


class TileResolver:
    """Resolves (x, y, planet) coordinates to OverworldRoom instances."""

    def __init__(
        self,
        planet_registry: PlanetRegistry,
        terrain_generators: dict[str, TerrainGenerator],
        room_cache: RoomCache,
        create_object_func: Callable[..., Any] | None = None,
        room_typeclass: str = "typeclasses.rooms.OverworldRoom",
    ) -> None:
        self._registry = planet_registry
        self._generators = terrain_generators
        self._cache = room_cache
        self._room_typeclass = room_typeclass
        # Injectable creation function for testability.
        # Defaults to evennia.utils.create.create_object at runtime.
        if create_object_func is not None:
            self._create_object = create_object_func
        else:
            self._create_object = self._default_create_object

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def resolve(self, x: int, y: int, planet: str) -> Any:
        """Return the OverworldRoom for (x, y, planet), creating on demand.

        Lookup order: cache → database → create new.
        Raises ValueError if coordinates are out of bounds.
        """
        self._validate_coords(x, y, planet)

        # 1. Cache lookup
        room = self._cache.get(x, y, planet)
        if room is not None:
            return room

        # 2. Database lookup
        room = self._db_lookup(x, y, planet)
        if room is not None:
            self._cache.put(x, y, planet, room)
            return room

        # 3. Create new room
        room = self._create_room(x, y, planet)
        self._cache.put(x, y, planet, room)
        return room

    def get_if_exists(self, x: int, y: int, planet: str) -> Any | None:
        """Return existing room or None. Does not create."""
        # 1. Cache lookup
        room = self._cache.get(x, y, planet)
        if room is not None:
            return room

        # 2. Database lookup
        room = self._db_lookup(x, y, planet)
        if room is not None:
            self._cache.put(x, y, planet, room)
            return room

        return None

    def get_cached(self, x: int, y: int, planet: str) -> Any | None:
        """Return a room only if it's in the cache. No DB query.

        Used by the map renderer and fog system for fast lookups
        during rendering — avoids hundreds of DB queries per frame.
        """
        return self._cache.get(x, y, planet)

    def get_or_generate_terrain(
        self, x: int, y: int, planet: str
    ) -> tuple[str, str | None]:
        """Return (terrain_type, resource_type) without creating a room.

        If a room exists, reads from the room. Otherwise queries
        TerrainGenerator. Used by the Procedural_Map_Renderer.
        """
        room = self.get_if_exists(x, y, planet)
        if room is not None:
            terrain = room.terrain_type if hasattr(room, "terrain_type") else "unknown"
            resource = None
            rn = room.resource_node if hasattr(room, "resource_node") else None
            if rn:
                resource = rn.get("resource_type")
            return terrain, resource

        # No room — use terrain generator
        gen = self._generators.get(planet)
        if gen is not None:
            return gen.get_terrain_and_resource(x, y)

        return "unknown", None

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _validate_coords(self, x: int, y: int, planet: str) -> None:
        """Raise ValueError if (x, y) is out of bounds for the planet."""
        if not self._registry.is_valid_coordinate(x, y, planet):
            raise ValueError(
                f"Coordinates ({x}, {y}) are out of bounds for planet '{planet}'"
            )

    def _create_room(self, x: int, y: int, planet: str) -> Any:
        """Create a new OverworldRoom with correct attributes and tags."""
        gen = self._generators.get(planet)
        if gen is not None:
            terrain_type, resource_type = gen.get_terrain_and_resource(x, y)
        else:
            terrain_type = "unknown"
            resource_type = None

        space = self._registry.get_space(planet)
        persistence_type = space.persistence_type

        # Build resource_node_data
        resource_node_data = None
        if resource_type:
            resource_node_data = {
                "resource_type": resource_type,
                "depleted": False,
                "respawn_counter": 0,
            }

        key = f"{terrain_type} ({x},{y})"

        room = self._create_object(
            typeclass=self._room_typeclass,
            key=key,
        )

        # Set Attributes
        room.db.x = x
        room.db.y = y
        room.db.planet = planet
        room.db.resource_node_data = resource_node_data
        room.db.created_tick = 0  # stamped by GC caller; 0 = unknown age

        # Set Tags
        room.tags.add("overworld_tile", category="room_type")
        room.tags.add(terrain_type, category="terrain")
        room.tags.add(persistence_type, category="persistence_type")
        room.tags.add(f"{x}:{y}:{planet}", category="coord")
        room.tags.add(str(x), category="coord_x")
        room.tags.add(str(y), category="coord_y")
        room.tags.add(planet, category="coord_planet")

        return room

    def _db_lookup(self, x: int, y: int, planet: str) -> Any | None:
        """Query the database for an existing room at (x, y, planet).

        Uses a single compound tag for fast single-index lookup.
        Falls back to the 3-tag query for rooms created before the
        compound tag was added.
        """
        try:
            from typeclasses.rooms import OverworldRoom

            compound_key = f"{x}:{y}:{planet}"
            results = OverworldRoom.objects.filter_family(
                db_tags__db_key=compound_key,
                db_tags__db_category="coord",
            )
            if results.exists():
                return results.first()

            # Fallback: legacy 3-tag query for old rooms
            results = OverworldRoom.objects.filter_family(
                db_tags__db_key=str(x),
                db_tags__db_category="coord_x",
            ).filter(
                db_tags__db_key=str(y),
                db_tags__db_category="coord_y",
            ).filter(
                db_tags__db_key=planet,
                db_tags__db_category="coord_planet",
            )
            if results.exists():
                room = results.first()
                # Backfill compound tag for future lookups
                room.tags.add(compound_key, category="coord")
                return room
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    #  Default creation function
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_create_object(**kwargs) -> Any:
        """Default room creation using Evennia's create_object."""
        from evennia.utils.create import create_object

        return create_object(**kwargs)
