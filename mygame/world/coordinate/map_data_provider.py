"""
Map Data Provider — generates structured tile data for the graphical webclient.

Produces a JSON-serializable dict describing the visible map area,
including terrain types, buildings, players, fog state, and player
position. The webclient Canvas renderer consumes this data.

The provider reuses the same fog/visibility logic as the ASCII renderer
but outputs structured data instead of colored text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from world.coordinate.fog_of_war import _get_planet, _get_coord

if TYPE_CHECKING:
    from world.coordinate.fog_of_war import FogOfWarSystem
    from world.coordinate.terrain_generator import TerrainGenerator
    from world.coordinate.tile_resolver import TileResolver


class MapDataProvider:
    """Generates structured map data for the graphical webclient."""

    def __init__(
        self,
        tile_resolver: TileResolver,
        fog_system: FogOfWarSystem,
        terrain_generators: dict[str, TerrainGenerator],
    ) -> None:
        self._tile_resolver = tile_resolver
        self._fog_system = fog_system
        self._terrain_generators = terrain_generators

    def get_map_data(
        self,
        player: Any,
        player_buildings: list[Any],
    ) -> dict:
        """Return a JSON-serializable dict of the player's visible map.

        Returns:
            {
                "player": {"x": int, "y": int, "planet": str},
                "bounds": {"min_x": int, "max_x": int, "min_y": int, "max_y": int},
                "vision_radius": int,
                "tiles": [
                    {"x": int, "y": int, "terrain": str, "state": str,
                     "building": {...} | null, "players": [...]}
                ]
            }

        Tile states: "visible", "fog", "unexplored"
        """
        planet = _get_planet(player)
        px = _get_coord(player, "coord_x")
        py = _get_coord(player, "coord_y")
        pvr = self._fog_system.player_vision_radius
        border = getattr(self._fog_system, "_map_border", 5)

        # Compute visibility
        visible_tiles = self._fog_system.get_visible_tiles(player, player_buildings)
        self._fog_system.update_discovery(player, visible_tiles, self._tile_resolver)
        discovered = self._fog_system.get_discovered_tile_set(player)
        buildings_mem = self._fog_system.get_discovered_buildings_map(player)

        # Bounds
        min_x = px - pvr - border
        max_x = px + pvr + border
        min_y = py - pvr - border
        max_y = py + pvr + border

        # Preload building rooms
        self._tile_resolver.preload_area(min_x, max_x, min_y, max_y, planet)

        gen = self._terrain_generators.get(planet)
        get_cached = self._tile_resolver.get_cached
        tiles = []

        for y in range(max_y, min_y - 1, -1):
            for x in range(min_x, max_x + 1):
                coord = (x, y)
                terrain = gen.get_terrain(x, y) if gen else "unknown"

                if coord in visible_tiles:
                    tile_data = self._visible_tile(
                        x, y, planet, terrain, player, get_cached
                    )
                elif coord in discovered:
                    tile_data = self._fog_tile(x, y, terrain, buildings_mem)
                else:
                    tile_data = {"x": x, "y": y, "terrain": terrain, "state": "unexplored"}

                tiles.append(tile_data)

        return {
            "player": {"x": px, "y": py, "planet": planet},
            "bounds": {
                "min_x": min_x, "max_x": max_x,
                "min_y": min_y, "max_y": max_y,
            },
            "vision_radius": pvr,
            "tiles": tiles,
        }

    def _visible_tile(self, x, y, planet, terrain, player, get_cached):
        """Build tile data for a fully visible tile."""
        tile = {"x": x, "y": y, "terrain": terrain, "state": "visible"}

        room = get_cached(x, y, planet)
        if room is not None:
            # Check for building
            bld = getattr(room, "building", None)
            if bld is not None:
                owner = None
                if hasattr(bld, "attributes") and hasattr(bld.attributes, "get"):
                    owner = bld.attributes.get("owner", default=None)
                owner_id = getattr(owner, "id", None) if owner else None
                player_id = getattr(player, "id", None)
                tile["building"] = {
                    "type": bld.attributes.get("building_type", default="??") if hasattr(bld, "attributes") else "??",
                    "level": bld.attributes.get("building_level", default=1) if hasattr(bld, "attributes") else 1,
                    "own": owner_id is not None and owner_id == player_id,
                    "name": getattr(bld, "key", "?"),
                }

            # Check for other players (by coordinate match)
            players_here = []
            for obj in getattr(room, "contents", []):
                if hasattr(obj, "has_account") and obj.has_account:
                    ox = _get_coord(obj, "coord_x") if hasattr(obj, "db") else None
                    oy = _get_coord(obj, "coord_y") if hasattr(obj, "db") else None
                    if ox == x and oy == y and obj is not player:
                        players_here.append(getattr(obj, "key", "?"))
            if players_here:
                tile["players"] = players_here

        return tile

    def _fog_tile(self, x, y, terrain, buildings_mem):
        """Build tile data for a fog-of-war tile."""
        tile = {"x": x, "y": y, "terrain": terrain, "state": "fog"}
        entry = buildings_mem.get((x, y))
        if entry:
            tile["building"] = {
                "type": entry.get("building_type", "??"),
                "own": False,
            }
        return tile
